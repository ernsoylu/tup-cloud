import { Badge, Button, Group, Progress, ScrollArea, Text } from '@mantine/core'
import { useMemo } from 'react'
import { formatBytes, useStore } from '../store'
import type { Transfer } from '../types'

const STATUS_COLOR: Record<Transfer['status'], string> = {
  queued: 'gray',
  running: 'blue',
  done: 'green',
  failed: 'red',
  skipped: 'yellow',
}

export default function Transfers() {
  const transfers = useStore((s) => s.transfers)
  const clearFinished = useStore((s) => s.clearFinishedTransfers)

  const list = useMemo(
    () => Object.values(transfers).sort((a, b) => (a.created_at < b.created_at ? 1 : -1)),
    [transfers],
  )
  const running = list.filter((t) => t.status === 'running' || t.status === 'queued').length
  const done = list.filter((t) => t.status === 'done').length

  return (
    <>
      <Group justify="space-between" px="md" py={4}>
        <Text size="xs" c="dimmed">
          Transfers — {running} active · {done} done
        </Text>
        <Button variant="subtle" size="compact-xs" onClick={() => void clearFinished()}>
          Clear finished
        </Button>
      </Group>
      <ScrollArea h={130}>
        {list.map((t) => {
          const active = t.status === 'running' || t.status === 'queued'
          return (
            <Group key={t.id} px="md" py={3} gap="sm" wrap="nowrap">
              <Text size="xs" w={230} truncate title={t.file_name}>
                {t.kind === 'cache' ? '⤓' : '⇪'} {t.file_name}
              </Text>
              <Text size="xs" c="dimmed" ff="monospace" w={130} truncate>
                {t.dest_dir}
              </Text>
              {active ? (
                <>
                  <Progress
                    value={t.size > 0 ? (t.sent / t.size) * 100 : 0}
                    size="sm"
                    style={{ flex: 1 }}
                    animated={t.status === 'running'}
                  />
                  <Text size="xs" c="dimmed" ff="monospace" w={170} ta="right">
                    {formatBytes(t.sent) || '0 B'} / {formatBytes(t.size)}
                  </Text>
                </>
              ) : (
                <Group gap="xs" style={{ flex: 1 }} wrap="nowrap">
                  <Badge variant="light" color={STATUS_COLOR[t.status]} size="xs" tt="none">
                    {t.status}
                  </Badge>
                  {t.error && (
                    <Text size="xs" c="dimmed" truncate title={t.error}>
                      {t.error}
                    </Text>
                  )}
                </Group>
              )}
            </Group>
          )
        })}
      </ScrollArea>
    </>
  )
}
