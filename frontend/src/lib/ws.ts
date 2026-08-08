import type { StreamMessage } from './types'

type Listener = (message: StreamMessage) => void
type StatusListener = (status: ConnectionStatus) => void

export type ConnectionStatus = 'connecting' | 'open' | 'closed' | 'reconnecting'

function resolveUrl(): string {
  const configured = import.meta.env.VITE_WS_URL as string | undefined
  if (configured) return `${configured.replace(/\/$/, '')}/ws/stream`
  // file:// has no host to inherit, so the packaged desktop app points at the
  // local backend directly.
  if (window.location.protocol === 'file:') return 'ws://localhost:8000/ws/stream'
  // Same-origin in dev — Vite proxies /ws through to the backend.
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/stream`
}

/**
 * Resilient stream client.
 *
 * Reconnects with capped exponential backoff and replays the last subscription
 * on reconnect, so a dropped socket restores the exact view the trader was
 * looking at without them touching anything.
 */
export class StreamClient {
  private socket: WebSocket | null = null
  private listeners = new Set<Listener>()
  private statusListeners = new Set<StatusListener>()
  private reconnectAttempts = 0
  private reconnectTimer: number | null = null
  private heartbeat: number | null = null
  private closedByUser = false
  private lastSubscription: { symbol: string; timeframe: string } | null = null
  private pendingSettings: Record<string, unknown>[] = []

  connect(): void {
    if (this.socket && (this.socket.readyState === WebSocket.OPEN ||
                        this.socket.readyState === WebSocket.CONNECTING)) return

    this.closedByUser = false
    this.emitStatus(this.reconnectAttempts > 0 ? 'reconnecting' : 'connecting')

    const socket = new WebSocket(resolveUrl())
    this.socket = socket

    socket.onopen = () => {
      this.reconnectAttempts = 0
      this.emitStatus('open')
      if (this.lastSubscription) {
        this.send({ type: 'subscribe', ...this.lastSubscription })
      }
      this.pendingSettings.forEach((setting) => this.send(setting))
      this.pendingSettings = []
      this.heartbeat = window.setInterval(() => this.send({ type: 'ping' }), 25_000)
    }

    socket.onmessage = (event) => {
      let message: StreamMessage
      try {
        message = JSON.parse(event.data)
      } catch {
        return
      }
      this.listeners.forEach((listener) => listener(message))
    }

    socket.onclose = () => {
      this.clearHeartbeat()
      this.socket = null
      this.emitStatus('closed')
      if (!this.closedByUser) this.scheduleReconnect()
    }

    socket.onerror = () => socket.close()
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return
    const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 15_000)
    this.reconnectAttempts += 1
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, delay)
  }

  private clearHeartbeat(): void {
    if (this.heartbeat) {
      window.clearInterval(this.heartbeat)
      this.heartbeat = null
    }
  }

  send(payload: Record<string, unknown>): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(payload))
    } else if (payload.type !== 'ping' && payload.type !== 'subscribe') {
      // Settings sent while offline are replayed once the socket is back.
      this.pendingSettings.push(payload)
    }
  }

  subscribe(symbol: string, timeframe: string): void {
    this.lastSubscription = { symbol, timeframe }
    this.send({ type: 'subscribe', symbol, timeframe })
  }

  requestAnalysis(): void {
    this.send({ type: 'analyse' })
  }

  setAutoAnalysis(enabled: boolean): void {
    this.send({ type: 'set_auto_analysis', enabled })
  }

  setIncludeAi(enabled: boolean): void {
    this.send({ type: 'set_include_ai', enabled })
  }

  onMessage(listener: Listener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  onStatus(listener: StatusListener): () => void {
    this.statusListeners.add(listener)
    return () => this.statusListeners.delete(listener)
  }

  private emitStatus(status: ConnectionStatus): void {
    this.statusListeners.forEach((listener) => listener(status))
  }

  disconnect(): void {
    this.closedByUser = true
    this.clearHeartbeat()
    if (this.reconnectTimer) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.socket?.close()
    this.socket = null
  }
}

export const stream = new StreamClient()
