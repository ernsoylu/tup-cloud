import type {
  Drive,
  FileVersionItem,
  ObserverEventItem,
  Transfer,
  TrashItem,
  UserInfo,
  VfsEntry,
} from './types'

class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// 401s on these mean "bad credentials", not "expired session" — never auto-refresh.
const NO_REFRESH_PATHS = ['/api/auth/refresh', '/api/auth/verify', '/api/auth/request-code']

async function request<T>(path: string, init?: RequestInit, retried = false): Promise<T> {
  const response = await fetch(path, { credentials: 'include', ...init })
  if (response.status === 401 && !retried && !NO_REFRESH_PATHS.includes(path)) {
    const refreshed = await fetch('/api/auth/refresh', { method: 'POST', credentials: 'include' })
    if (refreshed.ok) return request<T>(path, init, true)
    throw new ApiError(401, 'Session expired')
  }
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      /* not json */
    }
    throw new ApiError(response.status, detail)
  }
  return (await response.json()) as T
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  botInfo: () => request<{ bot: string }>('/api/auth/bot'),
  requestCode: (identifier: string) =>
    request<{ challenge: string; bot: string }>('/api/auth/request-code', json({ identifier })),
  verifyCode: (challenge: string | null, code: string) =>
    request<UserInfo>('/api/auth/verify', json({ challenge, code })),
  me: () => request<UserInfo>('/api/auth/me'),
  logout: () => request<{ ok: boolean }>('/api/auth/logout', { method: 'POST' }),
  listUsers: () => request<import('./types').AdminUserRow[]>('/api/auth/users'),
  toggleBlock: (id: number) =>
    request<{ id: number; approved: boolean }>(`/api/auth/users/${id}/toggle-block`, {
      method: 'POST',
    }),

  drives: () => request<Drive[]>('/api/drives'),
  addDrive: (alias: string, chatId: string) =>
    request<Drive>('/api/drives', json({ alias, chat_id: chatId })),
  removeDrive: (alias: string) =>
    request<{ ok: boolean }>(`/api/drives/${encodeURIComponent(alias)}`, { method: 'DELETE' }),

  index: (chatId: string) => request<VfsEntry[]>(`/api/vfs/${chatId}`),
  mkdir: (chatId: string, path: string) =>
    request<{ ok: boolean }>(`/api/vfs/${chatId}/mkdir`, json({ path })),
  rm: (chatId: string, path: string) =>
    request<{ ok: boolean }>(`/api/vfs/${chatId}/rm`, json({ path })),
  rmdir: (chatId: string, path: string) =>
    request<{ ok: boolean }>(`/api/vfs/${chatId}/rmdir`, json({ path })),
  mv: (chatId: string, src: string, dest: string) =>
    request<{ ok: boolean }>(`/api/vfs/${chatId}/mv`, json({ src, dest })),
  cp: (chatId: string, src: string, dest: string) =>
    request<{ ok: boolean }>(`/api/vfs/${chatId}/cp`, json({ src, dest })),

  transfers: () => request<Transfer[]>('/api/transfers'),
  clearFinished: () => request<{ ok: boolean }>('/api/transfers/clear-finished', { method: 'POST' }),
  observerEvents: () => request<ObserverEventItem[]>('/api/observer/events'),
  warmCache: (entryId: number) =>
    request<Record<string, unknown>>(`/api/files/${entryId}/cache`, { method: 'POST' }),
  trashList: (chatId: string) => request<TrashItem[]>(`/api/vfs/${chatId}/trash`),
  trashRestore: (chatId: string, path: string) =>
    request<{ ok: boolean }>(`/api/vfs/${chatId}/trash/restore`, json({ path })),
  trashEmpty: (chatId: string) =>
    request<{ ok: boolean; purged: number }>(`/api/vfs/${chatId}/trash/empty`, {
      method: 'POST',
    }),
  listVersions: (entryId: number) =>
    request<FileVersionItem[]>(`/api/files/${entryId}/versions`),
  wopiSession: (entryId: number) =>
    request<{ url: string }>(`/api/files/${entryId}/wopi-session`, { method: 'POST' }),
  newOfficeFile: (chatId: string, path: string, docType: string) =>
    request<{ ok: boolean; id: number; file_name: string }>(
      '/api/files/office',
      json({ chat_id: chatId, path, doc_type: docType }),
    ),
  saveText: (chatId: string, path: string, content: string) =>
    request<{ ok: boolean; id: number; unchanged?: boolean }>(
      '/api/files/text',
      json({ chat_id: chatId, path, content }),
    ),
  editCaption: (entryId: number, caption: string) =>
    request<{ ok: boolean; user_caption: string; tags: string }>(
      `/api/files/${entryId}/caption`,
      { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ caption }) },
    ),
  cacheStats: () =>
    request<{ files: number; bytes: number; ttl_minutes: number }>('/api/cache/stats'),
}

export async function fetchText(entryId: number): Promise<string> {
  const response = await fetch(streamUrl(entryId), { credentials: 'include' })
  if (!response.ok) throw new ApiError(response.status, 'Could not load file content')
  return response.text()
}

export async function fetchVersionText(versionId: number): Promise<string> {
  const response = await fetch(`/api/files/versions/${versionId}/content`, {
    credentials: 'include',
  })
  if (!response.ok) throw new ApiError(response.status, 'Could not load that version')
  return response.text()
}

export function streamUrl(entryId: number): string {
  return `/api/files/${entryId}/stream`
}

export function thumbUrl(entryId: number): string {
  return `/api/files/${entryId}/thumb`
}

export function downloadUrl(entryId: number): string {
  return `/api/files/${entryId}/download`
}

export function exportUrl(entryId: number, format = 'pdf'): string {
  return `/api/files/${entryId}/export?format=${format}`
}

/** Multipart upload with browser-side progress; returns the server transfer. */
export function uploadFile(
  file: File,
  chatId: string,
  dest: string,
  onProgress: (sent: number, total: number) => void,
): Promise<Transfer> {
  return new Promise((resolve, reject) => {
    const form = new FormData()
    form.append('file', file, file.name)
    form.append('chat_id', chatId)
    form.append('dest', dest)
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/uploads')
    xhr.withCredentials = true
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded, e.total)
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as Transfer)
      } else {
        let detail = `Upload failed (${xhr.status})`
        try {
          detail = JSON.parse(xhr.responseText).detail ?? detail
        } catch {
          /* keep default */
        }
        reject(new ApiError(xhr.status, detail))
      }
    }
    xhr.onerror = () => reject(new ApiError(0, 'Network error during upload'))
    xhr.send(form)
  })
}

export { ApiError }
