import { Badge, Button, Center, Group, Loader, Modal, ScrollArea, Stack, Text, TypographyStylesProvider } from '@mantine/core'
import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, downloadUrl, fetchText, streamUrl } from '../api'
import { promptModal } from '../dialogs'
import { mimeFromName } from '../media'
import { isMarkdown } from '../ops'
import { formatBytes, useStore } from '../store'

/** Fetches a markdown file and renders it formatted (GFM: tables, task lists). */
function MarkdownBody({ entryId }: { entryId: number }) {
  const [text, setText] = useState<string | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    setText(null)
    setError('')
    fetchText(entryId)
      .then(setText)
      .catch((err) => setError((err as Error).message))
  }, [entryId])

  if (error) return <Text c="red" p="md">{error}</Text>
  if (text === null)
    return (
      <Center py="xl" w={600}>
        <Loader size="sm" />
      </Center>
    )
  return (
    <ScrollArea.Autosize mah="calc(100vh - 200px)" w="100%">
      <TypographyStylesProvider p="lg" pr="xl">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
      </TypographyStylesProvider>
    </ScrollArea.Autosize>
  )
}

export default function Preview() {
  const entry = useStore((s) => s.preview)
  const setPreview = useStore((s) => s.setPreview)
  const toast = useStore((s) => s.toast)
  const [warmed, setWarmed] = useState(false)

  useEffect(() => setWarmed(false), [entry?.id])

  if (!entry) return null
  const url = streamUrl(entry.id)
  const mime = entry.mime_type || mimeFromName(entry.file_name)
  // Server-side caching only helps seekable media; documents/images arrive whole.
  const isPlayable =
    entry.media_kind === 'video' ||
    entry.media_kind === 'audio' ||
    mime.startsWith('video/') ||
    mime.startsWith('audio/')

  let body
  if (isMarkdown(entry.file_name)) {
    body = <MarkdownBody entryId={entry.id} />
  } else if (entry.media_kind === 'photo' || mime.startsWith('image/')) {
    body = <img className="preview-media" src={url} alt={entry.file_name} />
  } else if (entry.media_kind === 'video' || mime.startsWith('video/')) {
    body = (
      <video className="preview-media" src={url} controls autoPlay playsInline>
        Your browser cannot play this video.
      </video>
    )
  } else if (entry.media_kind === 'audio' || mime.startsWith('audio/')) {
    body = (
      <Center py={40} w="100%">
        <audio style={{ width: '90%' }} src={url} controls autoPlay />
      </Center>
    )
  } else if (mime === 'application/pdf' || (mime.startsWith('text/') && entry.file_size < 1024 * 1024)) {
    body = <iframe className="preview-frame" src={url} title={entry.file_name} />
  } else {
    body = (
      <Text c="dimmed" p={50}>
        No inline preview for {mime || 'this file type'}. Download it instead.
      </Text>
    )
  }

  const duration = entry.duration
    ? `${Math.floor(entry.duration / 60)}:${String(entry.duration % 60).padStart(2, '0')}`
    : ''

  return (
    <Modal
      opened
      onClose={() => setPreview(null)}
      fullScreen
      title={
        <Stack gap={2}>
          <Text fw={600}>{entry.file_name}</Text>
          <Group gap={6}>
            <Text size="xs" c="dimmed" ff="monospace">
              {formatBytes(entry.file_size)}
              {entry.width && entry.height ? ` · ${entry.width}×${entry.height}` : ''}
              {duration ? ` · ${duration}` : ''}
            </Text>
            {entry.origin === 'observed' && (
              <Badge variant="light" color="grape" size="xs" tt="none">
                observed
              </Badge>
            )}
          </Group>
          <Group gap={4}>
            {entry.user_caption && (
              <Text size="xs" c="dimmed">
                “{entry.user_caption}”
              </Text>
            )}
            {entry.tags &&
              entry.tags.split(' ').map((t) => (
                <Badge key={t} variant="light" size="xs" tt="none">
                  #{t}
                </Badge>
              ))}
            <Button
              variant="subtle"
              size="compact-xs"
              onClick={async () => {
                const next = await promptModal({
                  title: 'Caption and tags',
                  label: 'Caption text; #hashtags become searchable tags',
                  placeholder: 'Quarterly report #work #2026',
                  initial: entry.user_caption,
                  confirmLabel: 'Save',
                })
                if (next === null) return
                try {
                  await api.editCaption(entry.id, next)
                  toast('info', 'Caption updated.')
                } catch (error) {
                  toast('error', (error as Error).message)
                }
              }}
            >
              {entry.user_caption || entry.tags ? 'Edit caption' : 'Add caption / tags'}
            </Button>
          </Group>
        </Stack>
      }
    >
      <Stack gap="sm" h="calc(100vh - 130px)">
        <Center style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>{body}</Center>
        <Group justify="flex-end">
          {isPlayable && (
            <Button
              variant="default"
              size="xs"
              disabled={warmed}
              onClick={async () => {
                try {
                  await api.warmCache(entry.id)
                  setWarmed(true)
                  toast('info', 'Caching on the server — seeking gets faster shortly.')
                } catch (error) {
                  toast('error', (error as Error).message)
                }
              }}
            >
              {warmed ? 'Caching…' : '⚡ Cache for fast playback'}
            </Button>
          )}
          <Button
            component="a"
            href={downloadUrl(entry.id)}
            target="_blank"
            rel="noreferrer"
            size="xs"
          >
            Download
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
