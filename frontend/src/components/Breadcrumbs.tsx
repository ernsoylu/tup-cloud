import { Anchor, Group, Text } from '@mantine/core'
import { useStore } from '../store'

/** The path bar: a clickable, monospace VFS path — tup's CLI heritage. */
export default function Breadcrumbs() {
  const currentDir = useStore((s) => s.currentDir)
  const setDir = useStore((s) => s.setDir)
  const parts = currentDir.split('/').filter(Boolean)

  return (
    <Group gap={2} wrap="nowrap" style={{ overflow: 'hidden' }}>
      <Anchor ff="monospace" size="sm" onClick={() => setDir('/')} underline="never">
        /
      </Anchor>
      {parts.map((part, index) => {
        const path = `/${parts.slice(0, index + 1).join('/')}/`
        return (
          <Group gap={2} key={path} wrap="nowrap">
            <Anchor
              ff="monospace"
              size="sm"
              onClick={() => setDir(path)}
              underline="never"
              style={{ whiteSpace: 'nowrap' }}
            >
              {part}
            </Anchor>
            {index < parts.length - 1 && (
              <Text ff="monospace" size="sm" c="dimmed">
                /
              </Text>
            )}
          </Group>
        )
      })}
    </Group>
  )
}
