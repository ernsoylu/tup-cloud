export interface UserInfo {
  id: number
  telegram_id: number
  username: string
  display_name: string
  role: 'admin' | 'user'
  chats: string[]
  default_chat_id: string
}

export interface Drive {
  alias: string
  chat_id: string
  title: string
}

export interface VfsEntry {
  id: number
  chat_id: string
  virtual_path: string
  file_name: string
  file_size: number
  file_hash: string
  telegram_message_id: number
  upload_timestamp: string
  mime_type: string
  media_kind: '' | 'photo' | 'video' | 'audio' | 'document'
  width: number | null
  height: number | null
  duration: number | null
  origin: 'upload' | 'observed'
  uploaded_by: string
  user_caption: string
  tags: string
}

export interface Transfer {
  id: string
  kind: 'upload' | 'cache' | 'browser'
  chat_id: string
  file_name: string
  dest_dir: string
  size: number
  sent: number
  status: 'queued' | 'running' | 'done' | 'failed' | 'skipped'
  error: string
  created_at: string
}

export interface ObserverEventItem {
  id: number
  chat_id: string
  message_id: number
  file_name: string
  virtual_path: string
  stage: 'detected' | 'analyzing' | 'indexed' | 'skipped' | 'failed'
  detail: string
  created_at: string
}

export interface FileVersionItem {
  id: number
  file_size: number
  file_hash: string
  saved_by: string
  created_at: string
}

export interface TrashItem extends VfsEntry {
  original_path: string
}

export interface BackupConfig {
  enabled: boolean
  chat_id: string | null
  period_hours: number
  keep_last: number
  last_backup_at: string
}

export interface BackupItem {
  id: number
  file_name: string
  file_size: number
  created_at: string
}

export interface AdminUserRow {
  id: number
  telegram_id: number
  username: string
  display_name: string
  role: string
  approved: boolean
  last_login: string
}

export type WsEvent =
  | { type: 'transfer'; chat_id: string; transfer: Transfer }
  | { type: 'index-changed'; chat_id: string }
  | { type: 'observer'; chat_id: string; event: ObserverEventItem }
  | { type: 'drives-changed' }
  | { type: 'cache-sweep'; removed: number; freed_bytes: number }
