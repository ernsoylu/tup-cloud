import { Badge, Divider, NavLink, ScrollArea, Text } from '@mantine/core'
import { IconTrash } from '@tabler/icons-react'
import { useMemo, useState } from 'react'
import { KindIcon } from '../media'
import { moveOrCopyNames } from '../ops'
import { allDirs, TRASH_DIR, trashCount, useStore } from '../store'

export default function Sidebar() {
  const entries = useStore((s) => s.entries)
  const currentDir = useStore((s) => s.currentDir)
  const setDir = useStore((s) => s.setDir)
  const [dropTarget, setDropTarget] = useState<string | null>(null)

  const dirs = useMemo(() => allDirs(entries), [entries])
  const trashed = useMemo(() => trashCount(entries), [entries])

  return (
    <ScrollArea h="100%" p="xs">
      <Text size="xs" tt="uppercase" c="dimmed" fw={600} px="sm" py={6} lts="0.06em">
        Folders
      </Text>
      {dirs.map((dir) => {
        const depth = dir === '/' ? 0 : dir.split('/').filter(Boolean).length
        const name = dir === '/' ? '/' : dir.split('/').filter(Boolean).pop()!
        return (
          <NavLink
            key={dir}
            label={
              <Text size="sm" truncate ff={dir === '/' ? 'monospace' : undefined}>
                {name}
              </Text>
            }
            leftSection={<KindIcon kind="folder" />}
            active={dir === currentDir}
            className={dropTarget === dir ? 'drop-target' : undefined}
            pl={10 + depth * 14}
            onClick={() => setDir(dir)}
            onDragOver={(e) => {
              if (e.dataTransfer.types.includes('application/x-tup')) {
                e.preventDefault()
                setDropTarget(dir)
              }
            }}
            onDragLeave={() => setDropTarget(null)}
            onDrop={(e) => {
              setDropTarget(null)
              const payload = e.dataTransfer.getData('application/x-tup')
              if (!payload) return
              e.preventDefault()
              const names = JSON.parse(payload) as string[]
              void moveOrCopyNames(names, dir, e.ctrlKey || e.altKey)
            }}
          />
        )
      })}
      <Divider my="xs" />
      <NavLink
        label={<Text size="sm">Recycle Bin</Text>}
        leftSection={<IconTrash size={16} stroke={1.6} color="var(--mantine-color-gray-5)" />}
        rightSection={
          trashed > 0 ? (
            <Badge size="xs" variant="light" color="gray">
              {trashed}
            </Badge>
          ) : undefined
        }
        active={currentDir.startsWith(TRASH_DIR)}
        onClick={() => setDir(TRASH_DIR)}
      />
    </ScrollArea>
  )
}
