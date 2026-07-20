import { Button, Center, Group, Loader, Text } from '@mantine/core'
import { IconArrowLeft } from '@tabler/icons-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import { useStore } from '../store'

/** Full-page Collabora Online (CODE) editor: fixed header, the document is
 * the CODE iframe. Saves flow back through the WOPI endpoints and pick up
 * version history exactly like markdown saves. */
export default function OfficeEditor() {
  const entry = useStore((s) => s.officeEntry)
  const setOfficeEntry = useStore((s) => s.setOfficeEntry)
  const refreshIndex = useStore((s) => s.refreshIndex)
  const toast = useStore((s) => s.toast)
  const [frameUrl, setFrameUrl] = useState<string | null>(null)

  useEffect(() => {
    setFrameUrl(null)
    if (!entry) return
    api
      .wopiSession(entry.id)
      .then((session) => setFrameUrl(session.url))
      .catch((error) => {
        toast('error', (error as Error).message)
        setOfficeEntry(null)
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
              setOfficeEntry(null)
              void refreshIndex(true)
            }}
          >
            Back
          </Button>
          <Text ff="monospace" size="sm" c="dimmed" truncate>
            {entry.virtual_path + entry.file_name}
          </Text>
        </Group>
        {/* Export/Download live inside Collabora itself (File → Export As). */}
      </Group>

      {frameUrl === null ? (
        <Center style={{ flex: 1 }}>
          <Loader size="sm" />
        </Center>
      ) : (
        <iframe
          title={entry.file_name}
          src={frameUrl}
          style={{ flex: 1, border: 0, width: '100%' }}
          allow="clipboard-read *; clipboard-write *"
        />
      )}
    </div>
  )
}
