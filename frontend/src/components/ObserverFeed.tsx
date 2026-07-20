import { Badge, Box, Group, ScrollArea, Stack, Text } from '@mantine/core'
import { useStore } from '../store'

const STAGE_COLOR: Record<string, string> = {
  detected: 'gray',
  analyzing: 'blue',
  indexed: 'green',
  skipped: 'yellow',
  failed: 'red',
}

export default function ObserverFeed() {
  const events = useStore((s) => s.observerEvents)
  const drives = useStore((s) => s.drives)

  const titleFor = (chatId: string) => drives.find((d) => d.chat_id === chatId)?.title ?? chatId

  return (
    <Stack gap={0} h="100%">
      <Box px="md" py="xs" style={{ borderBottom: '1px solid var(--mantine-color-dark-4)' }}>
        <Text fw={600} size="sm">
          Observer
        </Text>
        <Text size="xs" c="dimmed">
          Files sent in your groups land in /Other
        </Text>
      </Box>
      <ScrollArea style={{ flex: 1 }}>
        {events.length === 0 && (
          <Text c="dimmed" size="sm" p="md">
            Nothing observed yet. Send a file to one of your drive groups to see it flow in here.
          </Text>
        )}
        {events.map((e) => (
          <Box
            key={e.id}
            px="md"
            py={8}
            style={{ borderBottom: '1px solid var(--mantine-color-dark-6)' }}
          >
            <Group justify="space-between" wrap="nowrap" gap="xs">
              <Text size="sm" truncate title={e.file_name}>
                {e.file_name}
              </Text>
              <Badge variant="light" color={STAGE_COLOR[e.stage] ?? 'gray'} size="xs" tt="none">
                {e.stage}
              </Badge>
            </Group>
            <Text size="xs" c="dimmed" truncate>
              {titleFor(e.chat_id)} → <Text span size="xs" ff="monospace">{e.virtual_path}</Text>
              {e.detail ? ` · ${e.detail}` : ''}
            </Text>
          </Box>
        ))}
      </ScrollArea>
    </Stack>
  )
}
