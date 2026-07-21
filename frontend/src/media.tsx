/** File-type helpers shared by the listing, grid, and preview: extension →
 * MIME, MIME → media kind, kind → Tabler icon tinted with Mantine colors. */

import {
  Icon3dCubeSphere,
  IconFile,
  IconFileText,
  IconFileZip,
  IconFolder,
  IconMovie,
  IconMusic,
  IconPhoto,
} from '@tabler/icons-react'

export type IconKind =
  | 'folder'
  | 'photo'
  | 'video'
  | 'audio'
  | 'archive'
  | 'text'
  | 'cad'
  | 'document'

const EXT_MIME: Record<string, string> = {
  mp4: 'video/mp4',
  m4v: 'video/mp4',
  mov: 'video/quicktime',
  webm: 'video/webm',
  mkv: 'video/x-matroska',
  avi: 'video/x-msvideo',
  mp3: 'audio/mpeg',
  m4a: 'audio/mp4',
  ogg: 'audio/ogg',
  opus: 'audio/opus',
  flac: 'audio/flac',
  wav: 'audio/wav',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  gif: 'image/gif',
  webp: 'image/webp',
  heic: 'image/heic',
  svg: 'image/svg+xml',
  pdf: 'application/pdf',
  txt: 'text/plain',
  md: 'text/markdown',
  markdown: 'text/markdown',
  json: 'application/json',
  zip: 'application/zip',
  rar: 'application/vnd.rar',
  '7z': 'application/x-7z-compressed',
  gz: 'application/gzip',
  tar: 'application/x-tar',
  dwg: 'image/vnd.dwg',
  dxf: 'image/vnd.dxf',
  stl: 'model/stl',
  step: 'model/step',
  stp: 'model/step',
  obj: 'model/obj',
  iges: 'model/iges',
  igs: 'model/iges',
}

const CAD_EXTS = new Set(['dwg', 'dxf', 'step', 'stp', 'stl', 'obj', 'iges', 'igs'])

/** Files opened in the embedded Signex EDA. Only the single-file formats the
 * web build can WOPI-open: schematics round-trip, PCBs open read-only.
 * Projects / symbols / footprints (.snxprj/.snxsym/.snxfpt) stay downloads. */
const EDA_EXTS = new Set(['snxsch', 'snxpcb'])

export function isEda(name: string): boolean {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  return EDA_EXTS.has(ext)
}

/** Files opened in the embedded OpenCADStudio. */
export function isCad(name: string): boolean {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  return CAD_EXTS.has(ext)
}

const OFFICE_EXTS = new Set([
  'docx', 'doc', 'odt', 'rtf',
  'xlsx', 'xls', 'ods', 'csv',
  'pptx', 'ppt', 'odp',
  'odg', 'vsd', 'vsdx',
])

/** Files edited through Collabora Online rather than the markdown editor. */
export function isOffice(name: string): boolean {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  return OFFICE_EXTS.has(ext)
}

export function mimeFromName(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  return EXT_MIME[ext] ?? ''
}

export function kindFromMime(mime: string): IconKind {
  if (!mime) return 'document'
  if (mime.startsWith('image/')) return 'photo'
  if (mime.startsWith('video/')) return 'video'
  if (mime.startsWith('audio/')) return 'audio'
  if (mime.startsWith('text/') || mime === 'application/json') return 'text'
  if (
    mime === 'application/zip' ||
    mime === 'application/gzip' ||
    mime === 'application/x-tar' ||
    mime === 'application/vnd.rar' ||
    mime === 'application/x-7z-compressed'
  )
    return 'archive'
  return 'document'
}

/** Icon kind for a file: trust indexed media kind when it names real media,
 * otherwise infer from the extension so untyped files still look right. */
export function iconKindFor(fileName: string, mediaKind: string): IconKind {
  if (isCad(fileName) || isEda(fileName)) return 'cad'
  if (mediaKind === 'photo' || mediaKind === 'video' || mediaKind === 'audio') return mediaKind
  return kindFromMime(mimeFromName(fileName))
}

const ICON_COMPONENT: Record<IconKind, typeof IconFile> = {
  folder: IconFolder,
  cad: Icon3dCubeSphere,
  photo: IconPhoto,
  video: IconMovie,
  audio: IconMusic,
  archive: IconFileZip,
  text: IconFileText,
  document: IconFile,
}

const ICON_COLOR: Record<IconKind, string> = {
  folder: 'var(--mantine-color-blue-4)',
  cad: 'var(--mantine-color-indigo-4)',
  photo: 'var(--mantine-color-teal-4)',
  video: 'var(--mantine-color-grape-4)',
  audio: 'var(--mantine-color-orange-4)',
  archive: 'var(--mantine-color-yellow-5)',
  text: 'var(--mantine-color-cyan-4)',
  document: 'var(--mantine-color-gray-5)',
}

export function KindIcon({ kind, size = 16 }: { kind: IconKind; size?: number }) {
  const Icon = ICON_COMPONENT[kind]
  return (
    <Icon
      size={size}
      stroke={1.6}
      color={ICON_COLOR[kind]}
      style={{ verticalAlign: 'text-bottom', flexShrink: 0 }}
    />
  )
}

export function FileIcon({
  name,
  mediaKind,
  size = 16,
}: {
  name: string
  mediaKind: string
  size?: number
}) {
  return <KindIcon kind={iconKindFor(name, mediaKind)} size={size} />
}

const EXT_LABEL: Record<string, string> = {
  docx: 'document', doc: 'document', odt: 'document', rtf: 'document',
  xlsx: 'spreadsheet', xls: 'spreadsheet', ods: 'spreadsheet', csv: 'spreadsheet',
  pptx: 'presentation', ppt: 'presentation', odp: 'presentation',
  odg: 'drawing', vsd: 'drawing', vsdx: 'drawing',
  md: 'markdown', markdown: 'markdown',
  pdf: 'pdf',
  txt: 'text', json: 'text',
  zip: 'archive', rar: 'archive', '7z': 'archive', gz: 'archive', tar: 'archive',
  dwg: 'cad', dxf: 'cad', step: 'cad', stp: 'cad', stl: 'cad', obj: 'cad', iges: 'cad', igs: 'cad',
  snxsch: 'eda', snxpcb: 'eda', snxprj: 'eda', snxsym: 'eda', snxfpt: 'eda', snxlib: 'eda',
}

/** Human-friendly type shown in the Kind column: category plus the concrete
 * format, e.g. "document (docx)" vs "document (odt)" — not Telegram's routing
 * kind, where every non-media file is just a "document". */
export function kindLabel(fileName: string, mediaKind: string): string {
  const ext = fileName.includes('.') ? fileName.split('.').pop()!.toLowerCase() : ''
  let base: string
  if (mediaKind === 'photo' || mediaKind === 'video' || mediaKind === 'audio') {
    base = mediaKind
  } else if (ext in EXT_LABEL) {
    base = EXT_LABEL[ext]
  } else {
    const mime = mimeFromName(fileName)
    if (mime.startsWith('text/')) base = 'text'
    else if (mime.startsWith('image/')) base = 'photo'
    else base = 'file'
  }
  if (ext && ext !== base && ext.length <= 8) return `${base} (${ext})`
  return base
}
