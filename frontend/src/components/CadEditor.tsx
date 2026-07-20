import { Button, Center, Group, Loader, Text } from '@mantine/core'
import { IconArrowLeft, IconUpload } from '@tabler/icons-react'
import { useEffect, useRef, useState } from 'react'
import { api, replaceFile } from '../api'
import { useStore } from '../store'

/** Full-page OpenCADStudio (self-hosted, WOPI-patched fork) for CAD files.
 *
 * The session URL carries WOPI parameters: the studio fetches the drawing
 * itself on load, and Ctrl+S / Save inside the studio uploads straight back
 * through the shared save pipeline (previous revision kept as a version).
 * The manual "save exported copy" remains for formats the studio exports via
 * browser download (STL, STEP, PDF…). */
export default function CadEditor() {
  const entry = useStore((s) => s.cadEntry)
  const setCadEntry = useStore((s) => s.setCadEntry)
  const refreshIndex = useStore((s) => s.refreshIndex)
  const toast = useStore((s) => s.toast)
  const fileInput = useRef<HTMLInputElement>(null)
  const [saving, setSaving] = useState(false)
  const [frameUrl, setFrameUrl] = useState<string | null>(null)

  useEffect(() => {
    setFrameUrl(null)
    if (!entry) return
    api
      .cadSession(entry.id)
      .then((session) => setFrameUrl(session.url))
      .catch((error) => {
        toast('error', (error as Error).message)
        setCadEntry(null)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entry?.id])

  if (!entry) return null

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 300,
        background: 'var(--mantine-color-body)',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <Group
        px="md"
        py={8}
        justify="space-between"
        wrap="nowrap"
        style={{ borderBottom: '1px solid var(--mantine-color-dark-4)' }}
      >
        <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
          <Button
            variant="subtle"
            size="compact-sm"
            leftSection={<IconArrowLeft size={16} />}
            onClick={() => {
              setCadEntry(null)
              void refreshIndex(true)
            }}
          >
            Back
          </Button>
          <Text ff="monospace" size="sm" c="dimmed" truncate>
            {entry.virtual_path + entry.file_name}
          </Text>
          <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>
            Save in the studio (Ctrl+S) writes back to the drive
          </Text>
        </Group>
        <Group gap="xs" wrap="nowrap">
          <Button
            variant="default"
            size="compact-sm"
            loading={saving}
            leftSection={<IconUpload size={15} />}
            onClick={() => fileInput.current?.click()}
          >
            Save exported copy back
          </Button>
          <input
            ref={fileInput}
            type="file"
            hidden
            onChange={async (e) => {
              const file = e.target.files?.[0]
              e.target.value = ''
              if (!file) return
              setSaving(true)
              try {
                await replaceFile(entry.id, file)
                toast('info', `${entry.file_name} updated — previous revision kept as a version.`)
                await refreshIndex(true)
              } catch (error) {
                toast('error', (error as Error).message)
              } finally {
                setSaving(false)
              }
            }}
          />
        </Group>
      </Group>

      {frameUrl === null ? (
        <Center style={{ flex: 1 }}>
          <Loader size="sm" />
        </Center>
      ) : (
        <iframe
          title="OpenCADStudio"
          src={frameUrl}
          style={{ flex: 1, border: 0, width: '100%' }}
          allow="clipboard-read *; clipboard-write *"
        />
      )}
    </div>
  )
}
