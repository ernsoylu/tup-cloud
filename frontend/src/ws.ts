import { useStore } from './store'
import type { WsEvent } from './types'

let socket: WebSocket | null = null
let retryDelay = 1000
let keepalive: number | undefined

export function connectWs(): void {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING))
    return
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
  socket = new WebSocket(`${protocol}://${location.host}/ws`)

  socket.onopen = () => {
    retryDelay = 1000
    keepalive = window.setInterval(() => socket?.send('ping'), 25000)
  }

  socket.onmessage = (message) => {
    let event: WsEvent
    try {
      event = JSON.parse(message.data as string) as WsEvent
    } catch {
      return
    }
    const store = useStore.getState()
    switch (event.type) {
      case 'transfer':
        store.applyTransfer(event.transfer)
        break
      case 'index-changed':
        if (event.chat_id === store.currentDrive) void store.refreshIndex(true)
        break
      case 'observer':
        store.addObserverEvent(event.event)
        break
      case 'drives-changed':
        void store.loadDrives()
        break
      case 'cache-sweep':
        break
    }
  }

  socket.onclose = () => {
    if (keepalive !== undefined) window.clearInterval(keepalive)
    socket = null
    if (useStore.getState().user) {
      window.setTimeout(() => {
        // The access cookie may have expired while connected; refresh it first
        // so the reconnect handshake authenticates.
        void fetch('/api/auth/refresh', { method: 'POST', credentials: 'include' }).finally(
          connectWs,
        )
      }, retryDelay)
      retryDelay = Math.min(retryDelay * 2, 15000)
    }
  }

  socket.onerror = () => socket?.close()
}

export function disconnectWs(): void {
  socket?.close()
  socket = null
}
