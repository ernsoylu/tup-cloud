import { Button, Center, Group, Loader, Text } from '@mantine/core'
import { IconArrowLeft } from '@tabler/icons-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import { useStore } from '../store'

/** Full-page Signex EDA (self-hosted, WOPI-patched wasm fork) for .snx files.
 *
 * The session URL carries WOPI parameters: Signex fetches the document
 * itself on load, and Ctrl+S / File ▸ Save inside the editor uploads
 * straight back through the shared save pipeline (previous revision kept as
 * a version). Schematics round-trip; PCBs open read-only — the PCB editor
 * has no save path upstream yet. */
export default function EdaEditor() {
  const entry = useStore((s) => s.edaEntry)
  const setEdaEntry = useStore((s) => s.setEdaEntry)
  const refreshIndex = useStore((s) => s.refreshIndex)
  const toast = useStore((s) => s.toast)
  const [frameUrl, setFrameUrl] = useState<string | null>(null)

  useEffect(() => {
    setFrameUrl(null)
    if (!entry) return
    api
      .edaSession(entry.id)
      .then((session) => setFrameUrl(session.url))
      .catch((error) => {
        toast('error', (error as Error).message)
        setEdaEntry(null)
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
              setEdaEntry(null)
              void refreshIndex(true)
            }}
          >
            Back
          </Button>
          <Text ff="monospace" size="sm" c="dimmed" truncate>
            {entry.virtual_path + entry.file_name}
          </Text>
          <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>
            {entry.file_name.toLowerCase().endsWith('.snxpcb')
              ? 'PCB view is read-only in the web editor'
              : 'Save in Signex (Ctrl+S) writes back to the drive'}
          </Text>
        </Group>
      </Group>

      {frameUrl === null ? (
        <Center style={{ flex: 1 }}>
          <Loader size="sm" />
        </Center>
      ) : (
        <iframe
          title="Signex"
          src={frameUrl}
          style={{ flex: 1, border: 0, width: '100%' }}
          allow="clipboard-read *; clipboard-write *"
        />
      )}
    </div>
  )
}
