const fs = require("fs");
const http = require("http");
const net = require("net");
const path = require("path");
const { WebSocketServer, WebSocket } = require("ws");

const ROOT_DIR = path.resolve(__dirname, "..", "..");
const CONFIG_PATH = process.env.TWOMAN_CONFIG_PATH || path.join(__dirname, "config.json");
const RUNTIME_LOG_PATH = process.env.TWOMAN_RUNTIME_LOG_PATH || path.join(__dirname, "broker-runtime.log");
let TRACE_ENABLED = /^(1|true|yes|on|debug|verbose)$/i.test(process.env.TWOMAN_TRACE || "");
let DEBUG_STATS_ENABLED = /^(1|true|yes|on|debug|verbose)$/i.test(process.env.TWOMAN_DEBUG_STATS || "");
const HEARTBEAT_INTERVAL_MS = 20000;

const FRAME_HEADER_SIZE = 20;
const FRAME_OPEN_OK = 4;
const FRAME_FIN = 8;
const FRAME_DATA = 6;
const FRAME_WINDOW = 7;
const FRAME_PING = 10;
const FRAME_OPEN = 3;
const FRAME_OPEN_FAIL = 5;
const FRAME_RST = 9;
const FLAG_DATA_BULK = 1;
const LANE_CTL = "ctl";
const LANE_DATA = "data";
const DEFAULT_DATA_REPLAY_RESEND_MS = 750;

function trace(message) {
  if (!TRACE_ENABLED) {
    return;
  }
  const line = `[node-broker] ${message}\n`;
  try {
    fs.appendFileSync(RUNTIME_LOG_PATH, `${new Date().toISOString()} ${message}\n`, "utf8");
  } catch (_error) {
    // Best-effort runtime log only.
  }
  process.stderr.write(line);
}

function nowMs() {
  return Date.now();
}

function loadConfig() {
  return JSON.parse(fs.readFileSync(CONFIG_PATH, "utf8"));
}

function makeErrorPayload(message) {
  return Buffer.from(String(message || ""), "utf8");
}

function paddedPayload(payload, minimumSize) {
  let body = payload || Buffer.alloc(0);
  while (body.length < minimumSize) {
    body = Buffer.concat([body, encodeFrame({ typeId: FRAME_PING, flags: 0, streamId: 0, offset: nowMs(), payload: Buffer.alloc(0) })]);
  }
  return body;
}

function pingFramePayload() {
  return encodeFrame({ typeId: FRAME_PING, flags: 0, streamId: 0, offset: nowMs(), payload: Buffer.alloc(0) });
}

function encodeFrame(frame) {
  const payload = frame.payload || Buffer.alloc(0);
  const header = Buffer.alloc(FRAME_HEADER_SIZE);
  header.writeUInt8(frame.typeId >>> 0, 0);
  header.writeUInt8(frame.flags >>> 0, 1);
  header.writeUInt16BE(0, 2);
  header.writeUInt32BE(frame.streamId >>> 0, 4);
  header.writeBigUInt64BE(BigInt(frame.offset || 0), 8);
  header.writeUInt32BE(payload.length >>> 0, 16);
  return Buffer.concat([header, payload]);
}

class FrameDecoder {
  constructor() {
    this.buffer = Buffer.alloc(0);
  }

  feed(chunk) {
    if (!chunk || chunk.length === 0) {
      return [];
    }
    this.buffer = Buffer.concat([this.buffer, Buffer.from(chunk)]);
    const frames = [];
    while (this.buffer.length >= FRAME_HEADER_SIZE) {
      const typeId = this.buffer.readUInt8(0);
      const flags = this.buffer.readUInt8(1);
      const streamId = this.buffer.readUInt32BE(4);
      const offset = Number(this.buffer.readBigUInt64BE(8));
      const length = this.buffer.readUInt32BE(16);
      const total = FRAME_HEADER_SIZE + length;
      if (this.buffer.length < total) {
        break;
      }
      const payload = this.buffer.subarray(FRAME_HEADER_SIZE, total);
      frames.push({ typeId, flags, streamId, offset, payload });
      this.buffer = this.buffer.subarray(total);
    }
    return frames;
  }
}

class FrameQueue {
  constructor() {
    this.items = [];
    this.bufferedBytes = 0;
  }

  push(payload) {
    this.items.push(payload);
    this.bufferedBytes += payload.length;
  }

  shift() {
    const payload = this.items.shift() || null;
    if (payload) {
      this.bufferedBytes = Math.max(0, this.bufferedBytes - payload.length);
    }
    return payload;
  }
}

class PeerState {
  constructor(role, peerLabel, peerSessionId) {
    this.role = role;
    this.peerLabel = peerLabel;
    this.peerSessionId = peerSessionId;
    this.lastSeenMs = nowMs();
    this.channels = { ctl: null, data: null };
    this.flushScheduled = { ctl: false, data: false };
    this.ctlQueue = new FrameQueue();
    this.dataPriQueue = new FrameQueue();
    this.dataBulkQueue = new FrameQueue();
    this.dataReplay = { pri: [], bulk: [] };
    this.dataReplayByPayload = new Map();
    this.waiters = { ctl: [], data: [] };
    this.activeStreams = 0;
    this.openEventsMs = [];
  }

  touch() {
    this.lastSeenMs = nowMs();
  }

  bufferedBytesTotal() {
    return this.ctlQueue.bufferedBytes + this.dataPriQueue.bufferedBytes + this.dataBulkQueue.bufferedBytes;
  }

  notifyWaiters(lane) {
    const waiters = this.waiters[lane];
    this.waiters[lane] = [];
    for (const waiter of waiters) {
      waiter();
    }
  }

  waitForLane(lane, timeoutMs) {
    return new Promise((resolve) => {
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) {
          return;
        }
        settled = true;
        resolve(false);
      }, timeoutMs);
      this.waiters[lane].push(() => {
        if (settled) {
          return;
        }
        settled = true;
        clearTimeout(timer);
        resolve(true);
      });
    });
  }
}

class StreamState {
  constructor(helperSessionId, helperPeerLabel, helperStreamId, agentSessionId, agentStreamId) {
    this.helperSessionId = helperSessionId;
    this.helperPeerLabel = helperPeerLabel;
    this.helperStreamId = Number(helperStreamId);
    this.agentSessionId = agentSessionId;
    this.agentStreamId = Number(agentStreamId);
    this.createdAtMs = nowMs();
    this.lastSeenMs = this.createdAtMs;
    this.helperAckOffset = 0;
    this.agentAckOffset = 0;
    this.helperFinSeen = false;
    this.agentFinSeen = false;
  }

  touch() {
    this.lastSeenMs = nowMs();
  }
}

class BrokerState {
  constructor(config) {
    this.config = config;
    this.baseUri = String(config.base_uri || process.env.TWOMAN_BASE_URI || "").replace(/\/+$/, "");
    this.clientTokens = new Set(config.client_tokens || []);
    this.agentTokens = new Set(config.agent_tokens || []);
    this.peerTtlMs = Number(config.peer_ttl_seconds || 90) * 1000;
    this.streamTtlMs = Number(config.stream_ttl_seconds || 300) * 1000;
    this.maxLaneBytes = Number(config.max_lane_bytes || 16 * 1024 * 1024);
    this.maxPeerBufferedBytes = Number(
      config.max_peer_buffered_bytes || Math.min(this.maxLaneBytes * 2, 32 * 1024 * 1024)
    );
    this.maxStreamsPerPeerSession = Math.max(1, Number(config.max_streams_per_peer_session || 256));
    this.maxOpenRatePerPeerSession = Math.max(1, Number(config.max_open_rate_per_peer_session || 120));
    this.openRateWindowMs = Math.max(1000, Number(config.open_rate_window_seconds || 10) * 1000);
    this.flushBackpressureBytes = Number(config.flush_backpressure_bytes || 512 * 1024);
    this.flushRetryDelayMs = Number(config.flush_retry_delay_ms || 5);
    this.dataReplayResendMs = Math.max(50, Number(config.data_replay_resend_ms || DEFAULT_DATA_REPLAY_RESEND_MS));
    this.downWaitMs = this.normalizeDownWaitMs(config.down_wait_ms || {});
    this.streamingDataDownHelper = Boolean(config.streaming_data_down_helper);
    this.peers = new Map();
    this.streamsByHelper = new Map();
    this.streamsByAgent = new Map();
    this.agentSessionId = "";
    this.agentPeerLabel = "";
    this.nextAgentStreamId = 1;
    this.metrics = {
      peer_connects: 0,
      peer_disconnects: 0,
      ws_messages_in: { ctl: 0, data: 0 },
      ws_bytes_in: { ctl: 0, data: 0 },
      ws_messages_out: { ctl: 0, data: 0 },
      ws_bytes_out: { ctl: 0, data: 0 },
      frames_in: { ctl: 0, pri: 0, bulk: 0 },
      frames_out: { ctl: 0, pri: 0, bulk: 0 },
      connect_probe: { ok: 0, fail: 0 }
    };
    this.recentEvents = [];
  }

  normalizeDownWaitMs(rawConfig) {
    const ctl = Math.max(50, Number(rawConfig.ctl || rawConfig.control || 1000));
    const data = Math.max(50, Number(rawConfig.data || 1000));
    return { ctl, data };
  }

  recordEvent(kind, details) {
    if (!DEBUG_STATS_ENABLED) {
      return;
    }
    this.recentEvents.push({
      ts: new Date().toISOString(),
      kind,
      ...details
    });
    if (this.recentEvents.length > 200) {
      this.recentEvents.splice(0, this.recentEvents.length - 200);
    }
  }

  peerKey(role, peerSessionId) {
    return `${role}:${peerSessionId}`;
  }

  streamHelperKey(peerSessionId, streamId) {
    return `${peerSessionId}:${streamId}`;
  }

  auth(role, token) {
    if (role === "helper") {
      return this.clientTokens.has(token);
    }
    if (role === "agent") {
      return this.agentTokens.has(token);
    }
    return false;
  }

  normalizePath(rawPath) {
    if (rawPath === "/health" || rawPath === "/pid" || rawPath === "/connect-probe") {
      return rawPath;
    }
    if (this.baseUri && rawPath.startsWith(this.baseUri)) {
      const suffix = rawPath.slice(this.baseUri.length);
      return suffix || "/";
    }
    return rawPath || "/";
  }

  ensurePeer(role, peerLabel, peerSessionId) {
    const key = this.peerKey(role, peerSessionId);
    let peer = this.peers.get(key);
    if (!peer) {
      peer = new PeerState(role, peerLabel, peerSessionId);
      this.peers.set(key, peer);
      this.metrics.peer_connects += 1;
      trace(`peer online role=${role} label=${peerLabel} session=${peerSessionId}`);
    }
    peer.touch();
    peer.peerLabel = peerLabel;
    if (role === "agent") {
      this.agentSessionId = peerSessionId;
      this.agentPeerLabel = peerLabel;
    }
    return peer;
  }

  bindChannel(role, peerLabel, peerSessionId, lane, ws) {
    const peer = this.ensurePeer(role, peerLabel, peerSessionId);
    peer.channels[lane] = ws;
    ws._twomanPeerKey = this.peerKey(role, peerSessionId);
    ws._twomanLane = lane;
    ws.isAlive = true;
    this.scheduleFlush(peer, lane);
    return peer;
  }

  unbindChannel(peerKey, lane, ws) {
    const peer = this.peers.get(peerKey);
    if (!peer) {
      return;
    }
    if (peer.channels[lane] === ws) {
      peer.channels[lane] = null;
      this.metrics.peer_disconnects += 1;
    }
  }

  queueFrame(role, peerSessionId, frame) {
    const peer = this.peers.get(this.peerKey(role, peerSessionId));
    if (!peer) {
      trace(`drop frame type=${frame.typeId} stream=${frame.streamId} role=${role} session=${peerSessionId} reason=no-peer`);
      this.recordEvent("queue_drop", {
        reason: "no-peer",
        role,
        peer_session_id: peerSessionId,
        type_id: frame.typeId,
        stream_id: frame.streamId
      });
      return false;
    }
    const encoded = encodeFrame(frame);
    if (this.maxPeerBufferedBytes && peer.bufferedBytesTotal() >= this.maxPeerBufferedBytes) {
      trace(`drop frame type=${frame.typeId} stream=${frame.streamId} role=${role} session=${peerSessionId} reason=peer-buffer-full`);
      this.recordEvent("queue_drop", {
        reason: "peer-buffer-full",
        role,
        peer_session_id: peerSessionId,
        type_id: frame.typeId,
        stream_id: frame.streamId
      });
      return false;
    }
    if (frame.typeId === FRAME_DATA) {
      const targetQueue = (frame.flags & FLAG_DATA_BULK) ? peer.dataBulkQueue : peer.dataPriQueue;
      if (targetQueue.bufferedBytes >= this.maxLaneBytes) {
        trace(`drop data stream=${frame.streamId} role=${role} session=${peerSessionId} reason=data-queue-full`);
        this.recordEvent("queue_drop", {
          reason: "data-queue-full",
          role,
          peer_session_id: peerSessionId,
          type_id: frame.typeId,
          stream_id: frame.streamId
        });
        return false;
      }
      const replayLane = (frame.flags & FLAG_DATA_BULK) ? "bulk" : "pri";
      const entry = {
        encoded,
        streamId: frame.streamId,
        endOffset: Number(frame.offset || 0) + encoded.readUInt32BE(16),
        sentAtMs: 0,
        replayLane
      };
      targetQueue.push(encoded);
      peer.dataReplay[replayLane].push(entry);
      peer.dataReplayByPayload.set(encoded, entry);
      this.metrics.frames_out[(frame.flags & FLAG_DATA_BULK) ? "bulk" : "pri"] += 1;
      peer.notifyWaiters(LANE_DATA);
      this.scheduleFlush(peer, LANE_DATA);
      return true;
    }
    if (peer.ctlQueue.bufferedBytes >= this.maxLaneBytes) {
      trace(`drop ctl type=${frame.typeId} stream=${frame.streamId} role=${role} session=${peerSessionId} reason=ctl-queue-full`);
      this.recordEvent("queue_drop", {
        reason: "ctl-queue-full",
        role,
        peer_session_id: peerSessionId,
        type_id: frame.typeId,
        stream_id: frame.streamId
      });
      return false;
    }
    peer.ctlQueue.push(encoded);
    if (frame.typeId !== FRAME_PING) {
      this.recordEvent("queue_ctl", {
        role,
        peer_session_id: peerSessionId,
        type_id: frame.typeId,
        stream_id: frame.streamId,
        payload_bytes: frame.payload ? frame.payload.length : 0
      });
    }
    this.metrics.frames_out.ctl += 1;
    peer.notifyWaiters(LANE_CTL);
    this.scheduleFlush(peer, LANE_CTL);
    return true;
  }

  laneProfile(lane) {
    if (lane === LANE_CTL) {
      return { maxBytes: 4096, maxFrames: 8, holdMs: 1, padMin: 1024 };
    }
    if (lane === "pri") {
      return { maxBytes: 16384, maxFrames: 8, holdMs: 3, padMin: 1024 };
    }
    return { maxBytes: 65536, maxFrames: 16, holdMs: 8, padMin: 0 };
  }

  async nextCtlPayload(peer, waitTimeoutMs) {
    let first = peer.ctlQueue.shift();
    if (!first) {
      const notified = await peer.waitForLane(LANE_CTL, waitTimeoutMs);
      if (!notified) {
        return pingFramePayload();
      }
      first = peer.ctlQueue.shift();
      if (!first) {
        return pingFramePayload();
      }
    }
    peer.touch();
    const firstTypeId = first.readUInt8(0);
    const firstStreamId = first.readUInt32BE(4);
    if (firstTypeId !== FRAME_PING) {
      this.recordEvent("dequeue_ctl", {
        role: peer.role,
        peer_session_id: peer.peerSessionId,
        type_id: firstTypeId,
        stream_id: firstStreamId,
        bytes: first.length
      });
    }
    const profile = this.laneProfile(LANE_CTL);
    const payloads = [first];
    let total = first.length;
    let frames = 1;
    const deadline = Date.now() + profile.holdMs;
    while (total < profile.maxBytes && frames < profile.maxFrames && Date.now() < deadline) {
      const next = peer.ctlQueue.shift();
      if (!next) {
        break;
      }
      payloads.push(next);
      total += next.length;
      frames += 1;
    }
    this.metrics.ws_bytes_out.ctl += 0;
    return paddedPayload(Buffer.concat(payloads), profile.padMin);
  }

  async nextDataPayload(peer, waitTimeoutMs) {
    let first = peer.dataPriQueue.shift();
    let sourceLane = "pri";
    if (!first) {
      first = peer.dataBulkQueue.shift();
      sourceLane = "bulk";
    }
    if (!first) {
      const notified = await peer.waitForLane(LANE_DATA, waitTimeoutMs);
      if (!notified) {
        return pingFramePayload();
      }
      first = peer.dataPriQueue.shift();
      sourceLane = "pri";
      if (!first) {
        first = peer.dataBulkQueue.shift();
        sourceLane = "bulk";
      }
      if (!first) {
        first = this.nextReplayPayload(peer, "pri") || this.nextReplayPayload(peer, "bulk");
        sourceLane = first ? (peer.dataReplayByPayload.get(first)?.replayLane || "bulk") : sourceLane;
        if (!first) {
          return pingFramePayload();
        }
      }
    }
    this.noteDataSent(peer, first);
    peer.touch();
    const profile = this.laneProfile(sourceLane);
    const queue = sourceLane === "pri" ? peer.dataPriQueue : peer.dataBulkQueue;
    const payloads = [first];
    let total = first.length;
    let frames = 1;
    const deadline = Date.now() + profile.holdMs;
    while (total < profile.maxBytes && frames < profile.maxFrames && Date.now() < deadline) {
      let next = queue.shift();
      if (!next) {
        next = this.nextReplayPayload(peer, sourceLane);
      }
      if (!next) {
        break;
      }
      this.noteDataSent(peer, next);
      payloads.push(next);
      total += next.length;
      frames += 1;
    }
    return profile.padMin > 0 ? paddedPayload(Buffer.concat(payloads), profile.padMin) : Buffer.concat(payloads);
  }

  noteDataSent(peer, payload) {
    const entry = peer.dataReplayByPayload.get(payload);
    if (!entry) {
      return;
    }
    entry.sentAtMs = nowMs();
  }

  nextReplayPayload(peer, lane) {
    const entries = peer.dataReplay[lane];
    const cutoff = nowMs() - this.dataReplayResendMs;
    for (const entry of entries) {
      if (entry.sentAtMs > 0 && entry.sentAtMs <= cutoff) {
        return entry.encoded;
      }
    }
    return null;
  }

  pruneAckedData(role, peerSessionId, streamId, ackOffset) {
    const peer = this.peers.get(this.peerKey(role, peerSessionId));
    if (!peer) {
      return;
    }
    for (const lane of ["pri", "bulk"]) {
      const retained = [];
      for (const entry of peer.dataReplay[lane]) {
        if (entry.streamId === streamId && entry.endOffset <= ackOffset) {
          peer.dataReplayByPayload.delete(entry.encoded);
          continue;
        }
        retained.push(entry);
      }
      peer.dataReplay[lane] = retained;
    }
  }

  clearStreamReplay(stream) {
    this.pruneAckedData("helper", stream.helperSessionId, stream.helperStreamId, Number.MAX_SAFE_INTEGER);
    this.pruneAckedData("agent", stream.agentSessionId, stream.agentStreamId, Number.MAX_SAFE_INTEGER);
  }

  scheduleFlush(peer, lane) {
    if (peer.flushScheduled[lane]) {
      return;
    }
    peer.flushScheduled[lane] = true;
    const loop = () => {
      const ws = peer.channels[lane];
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        peer.flushScheduled[lane] = false;
        return;
      }
      if (ws.bufferedAmount > this.flushBackpressureBytes) {
        setTimeout(loop, this.flushRetryDelayMs);
        return;
      }
      let payload = null;
      let sourceKind = lane;
      if (lane === LANE_CTL) {
        payload = peer.ctlQueue.shift();
      } else {
        payload = peer.dataPriQueue.shift();
        if (payload) {
          sourceKind = "pri";
        } else {
          payload = peer.dataBulkQueue.shift();
          sourceKind = "bulk";
        }
      }
      if (!payload) {
        peer.flushScheduled[lane] = false;
        return;
      }
      this.metrics.ws_messages_out[lane] += 1;
      this.metrics.ws_bytes_out[lane] += payload.length;
      ws.send(payload, { binary: true }, (error) => {
        if (error) {
          if (lane === LANE_CTL) {
            peer.ctlQueue.items.unshift(payload);
            peer.ctlQueue.bufferedBytes += payload.length;
          } else if (sourceKind === "pri") {
            peer.dataPriQueue.items.unshift(payload);
            peer.dataPriQueue.bufferedBytes += payload.length;
          } else {
            peer.dataBulkQueue.items.unshift(payload);
            peer.dataBulkQueue.bufferedBytes += payload.length;
          }
          peer.flushScheduled[lane] = false;
          trace(`flush error lane=${lane} peer=${peer.peerSessionId} error=${error}`);
          return;
        }
        setImmediate(loop);
      });
    };
    setImmediate(loop);
  }

  handleFrame(senderRole, senderPeerSessionId, lane, frame) {
    if (frame.typeId === FRAME_PING) {
      return;
    }
    this.recordEvent("frame_in", {
      sender_role: senderRole,
      sender_peer_session_id: senderPeerSessionId,
      lane,
      type_id: frame.typeId,
      stream_id: frame.streamId,
      payload_bytes: frame.payload ? frame.payload.length : 0
    });
    if (frame.typeId === FRAME_OPEN && senderRole === "helper") {
      this.handleOpen(senderPeerSessionId, frame);
      return;
    }
    const stream = senderRole === "helper"
      ? this.streamsByHelper.get(this.streamHelperKey(senderPeerSessionId, frame.streamId))
      : this.streamsByAgent.get(frame.streamId);
    if (!stream) {
      trace(`drop frame type=${frame.typeId} stream=${frame.streamId} from=${senderRole}/${senderPeerSessionId} lane=${lane} reason=unknown-stream`);
      return;
    }
    stream.touch();
    if (frame.typeId === FRAME_WINDOW) {
      if (senderRole === "helper") {
        stream.helperAckOffset += Number(frame.offset || 0);
        this.pruneAckedData("helper", stream.helperSessionId, stream.helperStreamId, stream.helperAckOffset);
      } else {
        stream.agentAckOffset += Number(frame.offset || 0);
        this.pruneAckedData("agent", stream.agentSessionId, stream.agentStreamId, stream.agentAckOffset);
      }
    }
    if (frame.typeId === FRAME_FIN) {
      if (senderRole === "helper") {
        stream.helperFinSeen = true;
      } else {
        stream.agentFinSeen = true;
      }
    }
    let targetRole;
    let targetPeerSessionId;
    let outboundStreamId;
    if (senderRole === "helper") {
      targetRole = "agent";
      targetPeerSessionId = stream.agentSessionId;
      outboundStreamId = stream.agentStreamId;
    } else {
      targetRole = "helper";
      targetPeerSessionId = stream.helperSessionId;
      outboundStreamId = stream.helperStreamId;
    }
    const outboundFrame = {
      typeId: frame.typeId,
      flags: frame.flags,
      streamId: outboundStreamId,
      offset: frame.offset,
      payload: frame.payload
    };
    const queued = this.queueFrame(targetRole, targetPeerSessionId, outboundFrame);
    if (queued) {
      this.recordEvent("frame_forward", {
        sender_role: senderRole,
        sender_peer_session_id: senderPeerSessionId,
        target_role: targetRole,
        target_peer_session_id: targetPeerSessionId,
        type_id: frame.typeId,
        source_stream_id: frame.streamId,
        target_stream_id: outboundStreamId
      });
    }
    if (!queued && senderRole === "helper") {
      this.queueFrame("helper", senderPeerSessionId, {
        typeId: FRAME_RST,
        flags: 0,
        streamId: frame.streamId,
        offset: 0,
        payload: makeErrorPayload("broker queue full")
      });
    }
    if (frame.typeId === FRAME_RST) {
      this.dropStream(stream);
      return;
    }
    if (frame.typeId === FRAME_FIN && stream.helperFinSeen && stream.agentFinSeen) {
      this.dropStream(stream);
    }
  }

  handleOpen(helperSessionId, frame) {
    let openError = "";
    let agentSessionId = this.agentSessionId;
    const helperPeer = this.peers.get(this.peerKey("helper", helperSessionId));
    const helperPeerLabel = helperPeer ? helperPeer.peerLabel : helperSessionId;
    if (!helperPeer) {
      openError = "helper session unavailable";
    } else {
      openError = this.reserveHelperOpen(helperPeer);
    }
    if (agentSessionId && !this.peers.has(this.peerKey("agent", agentSessionId))) {
      agentSessionId = "";
    }
    if (openError) {
      this.queueFrame("helper", helperSessionId, {
        typeId: FRAME_OPEN_FAIL,
        flags: 0,
        streamId: frame.streamId,
        offset: 0,
        payload: makeErrorPayload(openError)
      });
      return;
    }
    if (!agentSessionId) {
      this.queueFrame("helper", helperSessionId, {
        typeId: FRAME_OPEN_FAIL,
        flags: 0,
        streamId: frame.streamId,
        offset: 0,
        payload: makeErrorPayload("hidden agent unavailable")
      });
      return;
    }
    const agentStreamId = this.nextAgentStreamId++;
    const stream = new StreamState(helperSessionId, helperPeerLabel, frame.streamId, agentSessionId, agentStreamId);
    this.streamsByHelper.set(this.streamHelperKey(helperSessionId, frame.streamId), stream);
    this.streamsByAgent.set(agentStreamId, stream);
    helperPeer.activeStreams += 1;
    const agentPeer = this.peers.get(this.peerKey("agent", agentSessionId));
    if (agentPeer) {
      agentPeer.activeStreams += 1;
    }
    trace(`open helper=${helperPeerLabel}/${helperSessionId} helper_stream=${frame.streamId} agent_session=${agentSessionId} agent_stream=${agentStreamId}`);
    this.recordEvent("open_map", {
      helper_session_id: helperSessionId,
      helper_stream_id: frame.streamId,
      agent_session_id: agentSessionId,
      agent_stream_id: agentStreamId,
      helper_peer_label: helperPeerLabel
    });
    this.queueFrame("agent", agentSessionId, {
      typeId: FRAME_OPEN,
      flags: frame.flags,
      streamId: agentStreamId,
      offset: frame.offset,
      payload: frame.payload
    });
  }

  reserveHelperOpen(peer) {
    const currentMs = nowMs();
    const windowStart = currentMs - this.openRateWindowMs;
    peer.openEventsMs = peer.openEventsMs.filter((value) => value >= windowStart);
    if (peer.activeStreams >= this.maxStreamsPerPeerSession) {
      return "too many concurrent streams";
    }
    if (peer.openEventsMs.length >= this.maxOpenRatePerPeerSession) {
      return "too many new streams";
    }
    peer.openEventsMs.push(currentMs);
    return "";
  }

  dropStream(stream) {
    this.clearStreamReplay(stream);
    this.streamsByHelper.delete(this.streamHelperKey(stream.helperSessionId, stream.helperStreamId));
    this.streamsByAgent.delete(stream.agentStreamId);
    this.recordEvent("drop_stream", {
      helper_session_id: stream.helperSessionId,
      helper_stream_id: stream.helperStreamId,
      agent_session_id: stream.agentSessionId,
      agent_stream_id: stream.agentStreamId,
      helper_fin_seen: stream.helperFinSeen,
      agent_fin_seen: stream.agentFinSeen
    });
    const helperPeer = this.peers.get(this.peerKey("helper", stream.helperSessionId));
    if (helperPeer && helperPeer.activeStreams > 0) {
      helperPeer.activeStreams -= 1;
    }
    const agentPeer = this.peers.get(this.peerKey("agent", stream.agentSessionId));
    if (agentPeer && agentPeer.activeStreams > 0) {
      agentPeer.activeStreams -= 1;
    }
  }

  cleanup() {
    const peerCutoff = nowMs() - this.peerTtlMs;
    const streamCutoff = nowMs() - this.streamTtlMs;
    for (const [key, peer] of this.peers.entries()) {
      if (peer.lastSeenMs >= peerCutoff) {
        continue;
      }
      const staleStreams = [];
      for (const stream of this.streamsByAgent.values()) {
        if (stream.helperSessionId === peer.peerSessionId || stream.agentSessionId === peer.peerSessionId) {
          staleStreams.push(stream);
        }
      }
      for (const stream of staleStreams) {
        if (peer.role === "helper" && stream.agentSessionId) {
          this.queueFrame("agent", stream.agentSessionId, {
            typeId: FRAME_RST,
            flags: 0,
            streamId: stream.agentStreamId,
            offset: 0,
            payload: makeErrorPayload("peer expired")
          });
        }
        if (peer.role === "agent" && stream.helperSessionId) {
          this.queueFrame("helper", stream.helperSessionId, {
            typeId: FRAME_RST,
            flags: 0,
            streamId: stream.helperStreamId,
            offset: 0,
            payload: makeErrorPayload("peer expired")
          });
        }
        this.dropStream(stream);
      }
      this.peers.delete(key);
      if (peer.role === "agent" && this.agentSessionId === peer.peerSessionId) {
        this.agentSessionId = "";
        this.agentPeerLabel = "";
      }
    }
    for (const stream of Array.from(this.streamsByAgent.values())) {
      if (stream.lastSeenMs >= streamCutoff) {
        continue;
      }
      this.queueFrame("helper", stream.helperSessionId, {
        typeId: FRAME_RST,
        flags: 0,
        streamId: stream.helperStreamId,
        offset: 0,
        payload: makeErrorPayload("stream expired")
      });
      this.queueFrame("agent", stream.agentSessionId, {
        typeId: FRAME_RST,
        flags: 0,
        streamId: stream.agentStreamId,
        offset: 0,
        payload: makeErrorPayload("stream expired")
      });
      this.dropStream(stream);
    }
  }

  stats() {
    const buffered = { ctl: 0, pri: 0, bulk: 0 };
    const peers = [];
    for (const peer of this.peers.values()) {
      buffered.ctl += peer.ctlQueue.bufferedBytes;
      buffered.pri += peer.dataPriQueue.bufferedBytes;
      buffered.bulk += peer.dataBulkQueue.bufferedBytes;
      if (DEBUG_STATS_ENABLED) {
        peers.push({
          role: peer.role,
          peer_label: peer.peerLabel,
          peer_session_id: peer.peerSessionId,
          active_streams: peer.activeStreams,
          ctl_buffered_bytes: peer.ctlQueue.bufferedBytes,
          pri_buffered_bytes: peer.dataPriQueue.bufferedBytes,
          bulk_buffered_bytes: peer.dataBulkQueue.bufferedBytes,
          last_seen_age_ms: Math.max(0, nowMs() - peer.lastSeenMs),
          channel_open: {
            ctl: Boolean(peer.channels.ctl && peer.channels.ctl.readyState === WebSocket.OPEN),
            data: Boolean(peer.channels.data && peer.channels.data.readyState === WebSocket.OPEN)
          }
        });
      }
    }
    const payload = {
      ok: true,
      pid: process.pid,
      peers: this.peers.size,
      streams: this.streamsByAgent.size,
      agent_peer_label: this.agentPeerLabel,
      agent_session_id: this.agentSessionId,
      base_uri: this.baseUri,
      buffered_ctl_bytes: buffered.ctl,
      buffered_pri_bytes: buffered.pri,
      buffered_bulk_bytes: buffered.bulk,
      metrics: this.metrics
    };
    if (DEBUG_STATS_ENABLED) {
      payload.peer_details = peers;
      payload.stream_details = Array.from(this.streamsByAgent.values()).slice(0, 32).map((stream) => ({
        helper_session_id: stream.helperSessionId,
        helper_stream_id: stream.helperStreamId,
        agent_session_id: stream.agentSessionId,
        agent_stream_id: stream.agentStreamId,
        age_ms: Math.max(0, nowMs() - stream.createdAtMs),
        last_seen_age_ms: Math.max(0, nowMs() - stream.lastSeenMs)
      }));
      payload.recent_events = this.recentEvents.slice(-64);
    }
    return payload;
  }
}

const loadedConfig = loadConfig();
if (!TRACE_ENABLED && loadedConfig.trace_enabled) {
  TRACE_ENABLED = true;
}
if (!DEBUG_STATS_ENABLED && loadedConfig.debug_stats_enabled) {
  DEBUG_STATS_ENABLED = true;
}
const state = new BrokerState(loadedConfig);

function jsonResponse(res, statusCode, payload) {
  const body = Buffer.from(JSON.stringify(payload));
  res.writeHead(statusCode, {
    "Content-Type": "application/json",
    "Content-Length": String(body.length),
    "Cache-Control": "no-store"
  });
  res.end(body);
}

function connectionHeaders(req) {
  return {
    token: req.headers["x-relay-token"] || "",
    role: req.headers["x-twoman-role"] || "",
    peer: req.headers["x-twoman-peer"] || "",
    session: req.headers["x-twoman-session"] || ""
  };
}

async function handleConnectProbe(req, res, url) {
  const host = url.searchParams.get("host") || "";
  const port = Number(url.searchParams.get("port") || "0");
  if (!host || !port) {
    jsonResponse(res, 400, { error: "host and port are required" });
    return;
  }
  const started = Date.now();
  const socket = new net.Socket();
  socket.setTimeout(5000);
  const result = await new Promise((resolve) => {
    let settled = false;
    const finish = (payload) => {
      if (settled) {
        return;
      }
      settled = true;
      socket.destroy();
      resolve(payload);
    };
    socket.once("connect", () => finish({ ok: true }));
    socket.once("timeout", () => finish({ ok: false, error: "timeout" }));
    socket.once("error", (error) => finish({ ok: false, error: String(error.message || error) }));
    socket.connect(port, host);
  });
  if (result.ok) {
    state.metrics.connect_probe.ok += 1;
  } else {
    state.metrics.connect_probe.fail += 1;
  }
  jsonResponse(res, 200, {
    host,
    port,
    ok: result.ok,
    error: result.error || "",
    time_ms: Date.now() - started
  });
}

function handleChunkStream(res) {
  res.writeHead(200, {
    "Content-Type": "text/plain; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
    Connection: "keep-alive",
    "Transfer-Encoding": "chunked"
  });
  let count = 0;
  const timer = setInterval(() => {
    count += 1;
    res.write(`tick ${count} ${Date.now()}\n`);
    if (count >= 30) {
      clearInterval(timer);
      res.end("done\n");
    }
  }, 1000);
  res.on("close", () => clearInterval(timer));
}

async function handleUploadProbe(req, res) {
  const started = Date.now();
  const events = [];
  let totalBytes = 0;
  for await (const chunk of req) {
    totalBytes += chunk.length;
    events.push({
      offset_ms: Date.now() - started,
      chunk_bytes: chunk.length,
      total_bytes: totalBytes
    });
  }
  jsonResponse(res, 200, {
    ok: true,
    total_bytes: totalBytes,
    chunks: events.length,
    events
  });
}

function processInboundFrames(role, sessionId, externalLane, decoder, chunk) {
  const frames = decoder.feed(chunk);
  for (const frame of frames) {
    let logicalLane = externalLane;
    if (externalLane === LANE_DATA) {
      if (frame.typeId === FRAME_DATA) {
        logicalLane = (frame.flags & FLAG_DATA_BULK) ? "bulk" : "pri";
      } else {
        logicalLane = LANE_CTL;
      }
    }
    state.metrics.frames_in[logicalLane] += 1;
    state.handleFrame(role, sessionId, logicalLane, frame);
  }
  return frames.length;
}

async function handleLaneDownStream(peer, lane, res) {
  const started = Date.now();
  const maxDurationMs = 30000;
  const waitTimeoutMs = lane === LANE_CTL ? 1000 : 1000;
  let closed = false;
  res.on("close", () => {
    closed = true;
  });
  res.writeHead(200, {
    "Content-Type": "application/octet-stream",
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
    Connection: "keep-alive",
    "Transfer-Encoding": "chunked"
  });
  try {
    while (!closed && !res.writableEnded && (Date.now() - started) < maxDurationMs) {
      const payload = lane === LANE_CTL
        ? await state.nextCtlPayload(peer, waitTimeoutMs)
        : await state.nextDataPayload(peer, waitTimeoutMs);
      if (!payload || payload.length === 0) {
        continue;
      }
      if (!res.write(payload)) {
        await new Promise((resolve) => res.once("drain", resolve));
      }
      peer.touch();
    }
  } finally {
    if (!closed && !res.writableEnded) {
      res.end();
    }
  }
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  const route = state.normalizePath(url.pathname);
  if (route === "/health") {
    jsonResponse(res, 200, state.stats());
    return;
  }
  if (route === "/pid") {
    jsonResponse(res, 200, { pid: process.pid });
    return;
  }
  if (route === "/connect-probe") {
    await handleConnectProbe(req, res, url);
    return;
  }
  if (route === "/stream") {
    handleChunkStream(res);
    return;
  }
  if (route === "/upload_probe" && req.method === "POST") {
    await handleUploadProbe(req, res);
    return;
  }
  const parts = route.replace(/^\/+/, "").split("/");
  if (parts.length === 2 && (parts[0] === LANE_CTL || parts[0] === LANE_DATA) && (parts[1] === "up" || parts[1] === "down")) {
    const lane = parts[0];
    const direction = parts[1];
    const headers = connectionHeaders(req);
    if (!headers.role || !headers.peer || !headers.session || !state.auth(headers.role, headers.token)) {
      jsonResponse(res, 403, { error: "invalid role or token" });
      return;
    }
    const peer = state.ensurePeer(headers.role, headers.peer, headers.session);
    if (req.method === "POST" && direction === "up") {
      const decoder = new FrameDecoder();
      let frameCount = 0;
      for await (const chunk of req) {
        frameCount += processInboundFrames(headers.role, headers.session, lane, decoder, chunk);
      }
      jsonResponse(res, 200, { ok: true, frames: frameCount });
      return;
    }
    if (req.method === "GET" && direction === "down") {
      if (lane === LANE_DATA && headers.role === "helper" && state.streamingDataDownHelper) {
        await handleLaneDownStream(peer, lane, res);
        return;
      }
      const payload = lane === LANE_CTL
        ? await state.nextCtlPayload(peer, state.downWaitMs.ctl)
        : await state.nextDataPayload(peer, state.downWaitMs.data);
      res.writeHead(200, {
        "Content-Type": "application/octet-stream",
        "Content-Length": String(payload.length),
        "Cache-Control": "no-store"
      });
      res.end(payload);
      return;
    }
    jsonResponse(res, 405, { error: "method not allowed" });
    return;
  }
  jsonResponse(res, 404, { error: "not found", path: route });
});

const wss = new WebSocketServer({ noServer: true });
const echoWss = new WebSocketServer({ noServer: true });

server.on("upgrade", (req, socket, head) => {
  const url = new URL(req.url, "http://localhost");
  const route = state.normalizePath(url.pathname);
  if (route === "/ws-echo") {
    echoWss.handleUpgrade(req, socket, head, (ws) => {
      echoWss.emit("connection", ws, req);
    });
    return;
  }
  if (route !== `/${LANE_CTL}` && route !== `/${LANE_DATA}`) {
    socket.write("HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n");
    socket.destroy();
    return;
  }
  const lane = route.slice(1);
  const headers = connectionHeaders(req);
  if (!headers.role || !headers.peer || !headers.session || !state.auth(headers.role, headers.token)) {
    socket.write("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n");
    socket.destroy();
    return;
  }
  wss.handleUpgrade(req, socket, head, (ws) => {
    ws._twomanHeaders = headers;
    ws._twomanLane = lane;
    wss.emit("connection", ws, req);
  });
});

echoWss.on("connection", (ws) => {
  ws.on("message", (message, isBinary) => {
    ws.send(message, { binary: isBinary });
  });
});

wss.on("connection", (ws) => {
  const headers = ws._twomanHeaders;
  const lane = ws._twomanLane;
  const peer = state.bindChannel(headers.role, headers.peer, headers.session, lane, ws);
  const decoder = new FrameDecoder();
  ws.on("pong", () => {
    ws.isAlive = true;
  });
  ws.on("message", (message) => {
    const data = Buffer.isBuffer(message) ? message : Buffer.from(message);
    peer.touch();
    state.metrics.ws_messages_in[lane] += 1;
    state.metrics.ws_bytes_in[lane] += data.length;
    processInboundFrames(headers.role, headers.session, lane, decoder, data);
  });
  ws.on("close", () => {
    state.unbindChannel(ws._twomanPeerKey, lane, ws);
  });
  ws.on("error", (error) => {
    trace(`ws error role=${headers.role} session=${headers.session} lane=${lane} error=${error}`);
  });
  trace(`channel open role=${headers.role} label=${headers.peer} session=${headers.session} lane=${lane}`);
});

setInterval(() => {
  state.cleanup();
}, 10000).unref();

setInterval(() => {
  wss.clients.forEach((ws) => {
    if (ws.isAlive === false) {
      ws.terminate();
      return;
    }
    ws.isAlive = false;
    ws.ping();
  });
}, HEARTBEAT_INTERVAL_MS).unref();

server.listen(process.env.PORT || 3000, () => {
  trace(`listening pid=${process.pid} base_uri=${state.baseUri || "/"}`);
});

process.on("uncaughtException", (error) => {
  trace(`uncaughtException pid=${process.pid} error=${error && error.stack ? error.stack : error}`);
});

process.on("unhandledRejection", (reason) => {
  trace(`unhandledRejection pid=${process.pid} reason=${reason && reason.stack ? reason.stack : reason}`);
});

process.on("beforeExit", (code) => {
  trace(`beforeExit pid=${process.pid} code=${code}`);
});

process.on("exit", (code) => {
  trace(`exit pid=${process.pid} code=${code}`);
});

process.on("SIGTERM", () => {
  trace(`signal pid=${process.pid} sig=SIGTERM`);
});

process.on("SIGINT", () => {
  trace(`signal pid=${process.pid} sig=SIGINT`);
});
