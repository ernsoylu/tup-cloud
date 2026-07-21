import {
  Badge,
  Box,
  Button,
  Card,
  Center,
  Group,
  Image,
  Loader,
  Menu,
  SimpleGrid,
  Stack,
  Table,
  Text,
} from '@mantine/core'
import { MouseEvent as ReactMouseEvent, useMemo, useRef, useState } from 'react'
import { api, downloadUrl, exportUrl, thumbUrl } from '../api'
import { confirmModal } from '../dialogs'
import { FileIcon, isOffice, KindIcon, kindLabel } from '../media'
import {
  collectDropped,
  copySelection,
  createOfficeFile,
  deleteSelection,
  isMarkdown,
  moveOrCopyNames,
  newFolder,
  openRow,
  paste,
  restoreRows,
  rowPath,
} from '../ops'
import { buildRows, formatBytes, formatWhen, inTrash, Row, useStore } from '../store'

interface MenuState {
  x: number
  y: number
  row: Row | null
}

export default function FileList() {
  const entries = useStore((s) => s.entries)
  const currentDir = useStore((s) => s.currentDir)
  const currentDrive = useStore((s) => s.currentDrive)
  const filter = useStore((s) => s.filter)
  const view = useStore((s) => s.view)
  const recursive = useStore((s) => s.recursive)
  const selection = useStore((s) => s.selection)
  const setSelection = useStore((s) => s.setSelection)
  const loading = useStore((s) => s.loadingIndex)
  const uploadFiles = useStore((s) => s.uploadFiles)
  const refreshIndex = useStore((s) => s.refreshIndex)
  const toast = useStore((s) => s.toast)

  const [menu, setMenu] = useState<MenuState | null>(null)
  const [dropDir, setDropDir] = useState<string | null>(null)
  const [osDrag, setOsDrag] = useState(false)
  const lastClicked = useRef<string | null>(null)

  const trashView = inTrash(currentDir)

  const rows = useMemo(
    () => buildRows(entries, currentDir, filter, recursive),
    [entries, currentDir, filter, recursive],
  )

  const clickRow = (event: ReactMouseEvent, row: Row) => {
    event.stopPropagation()
    const next = new Set(selection)
    if (event.shiftKey && lastClicked.current) {
      const names = rows.map((r) => r.name)
      const a = names.indexOf(lastClicked.current)
      const b = names.indexOf(row.name)
      if (a !== -1 && b !== -1) {
        for (let i = Math.min(a, b); i <= Math.max(a, b); i++) next.add(names[i])
        setSelection(next)
        return
      }
    }
    if (event.metaKey || event.ctrlKey) {
      if (next.has(row.name)) next.delete(row.name)
      else next.add(row.name)
    } else {
      next.clear()
      next.add(row.name)
    }
    lastClicked.current = row.name
    setSelection(next)
  }

  const contextMenu = (event: ReactMouseEvent, row: Row | null) => {
    event.preventDefault()
    event.stopPropagation()
    if (row && !selection.has(row.name)) setSelection(new Set([row.name]))
    setMenu({ x: event.clientX, y: event.clientY, row })
  }

  const handleDrop = async (event: React.DragEvent, targetDir?: string) => {
    event.preventDefault()
    setDropDir(null)
    setOsDrag(false)
    if (trashView) return
    const internal = event.dataTransfer.getData('application/x-tup')
    if (internal) {
      const names = JSON.parse(internal) as string[]
      if (targetDir) void moveOrCopyNames(names, targetDir, event.ctrlKey || event.altKey)
      return
    }
    if (event.dataTransfer.items.length > 0) {
      const dropped = await collectDropped(event.dataTransfer.items)
      if (dropped.length === 0) {
        toast('info', 'Nothing to upload — hidden files are skipped.')
        return
      }
      const store = useStore.getState()
      if (targetDir && targetDir !== store.currentDir) {
        const prev = store.currentDir
        store.setDir(targetDir)
        await uploadFiles(dropped)
        store.setDir(prev)
      } else {
        await uploadFiles(dropped)
      }
    }
  }

  const dragProps = (row: Row) => {
    if (trashView) return {}
    return row.kind === 'file'
      ? {
          draggable: true,
          onDragStart: (e: React.DragEvent) => {
            const names = selection.has(row.name) ? [...selection] : [row.name]
            const fileNames = rows
              .filter((r) => r.kind === 'file' && names.includes(r.name))
              .map((r) => r.name)
            e.dataTransfer.setData('application/x-tup', JSON.stringify(fileNames))
            e.dataTransfer.effectAllowed = 'copyMove'
          },
        }
      : {
          onDragOver: (e: React.DragEvent) => {
            e.preventDefault()
            setDropDir(rowPath(row))
          },
          onDragLeave: () => setDropDir(null),
          onDrop: (e: React.DragEvent) => void handleDrop(e, rowPath(row)),
        }
  }

  const nameCell = (row: Row) => (
    <Text
      size="sm"
      span
      ff={row.name.includes('/') ? 'monospace' : undefined}
      style={{ userSelect: 'none', display: 'inline-flex', alignItems: 'center', gap: 8 }}
    >
      {row.kind === 'folder' ? (
        <KindIcon kind="folder" />
      ) : (
        <FileIcon name={row.name} mediaKind={row.mediaKind} />
      )}
      {row.name}
    </Text>
  )

  const emptyBin = async () => {
    if (!currentDrive) return
    const confirmed = await confirmModal({
      title: 'Empty Recycle Bin',
      message: 'Files and all their saved versions are deleted forever. This cannot be undone.',
      confirmLabel: 'Empty bin',
      danger: true,
    })
    if (!confirmed) return
    try {
      const result = await api.trashEmpty(currentDrive)
      toast('info', `Deleted ${result.purged} file(s) permanently.`)
      await refreshIndex(true)
    } catch (error) {
      toast('error', (error as Error).message)
    }
  }

  const body =
    view === 'details' ? (
      <Table stickyHeader highlightOnHover verticalSpacing={6}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Name</Table.Th>
            <Table.Th w={90} ta="right">
              Size
            </Table.Th>
            <Table.Th w={150}>Kind</Table.Th>
            <Table.Th w={170}>Tags</Table.Th>
            <Table.Th w={170}>Modified</Table.Th>
            <Table.Th w={100}>Origin</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((row) => (
            <Table.Tr
              key={row.name + row.kind}
              bg={selection.has(row.name) ? 'var(--mantine-color-blue-light)' : undefined}
              className={
                dropDir === rowPath(row) && row.kind === 'folder' ? 'drop-target' : undefined
              }
              onClick={(e) => clickRow(e, row)}
              onDoubleClick={() => openRow(row)}
              onContextMenu={(e) => contextMenu(e, row)}
              {...dragProps(row)}
            >
              <Table.Td>{nameCell(row)}</Table.Td>
              <Table.Td ta="right">
                <Text size="xs" c="dimmed" ff="monospace">
                  {row.kind === 'file' ? formatBytes(row.size) : ''}
                </Text>
              </Table.Td>
              <Table.Td>
                <Text size="xs" c="dimmed">
                  {row.kind === 'file' ? kindLabel(row.name, row.mediaKind) : ''}
                </Text>
              </Table.Td>
              <Table.Td>
                {row.tags &&
                  row.tags.split(' ').map((t) => (
                    <Badge key={t} variant="light" size="xs" mr={4} tt="none">
                      #{t}
                    </Badge>
                  ))}
              </Table.Td>
              <Table.Td>
                <Text size="xs" c="dimmed">
                  {formatWhen(row.modified)}
                </Text>
              </Table.Td>
              <Table.Td>
                {row.origin === 'observed' && (
                  <Badge variant="light" color="grape" size="xs" tt="none">
                    observed
                  </Badge>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    ) : (
      <SimpleGrid cols={{ base: 3, sm: 5, md: 6, lg: 8 }} spacing="sm" p="md">
        {rows.map((row) => (
          <Card
            key={row.name + row.kind}
            padding="xs"
            radius="md"
            withBorder={selection.has(row.name)}
            className={
              dropDir === rowPath(row) && row.kind === 'folder' ? 'drop-target' : undefined
            }
            style={{
              cursor: 'default',
              userSelect: 'none',
              background: selection.has(row.name)
                ? 'var(--mantine-color-blue-light)'
                : undefined,
            }}
            onClick={(e) => clickRow(e, row)}
            onDoubleClick={() => openRow(row)}
            onContextMenu={(e) => contextMenu(e, row)}
            {...dragProps(row)}
          >
            <GridThumb row={row} />
            <Text size="xs" ta="center" truncate mt={6} title={row.name}>
              {row.name}
            </Text>
          </Card>
        ))}
      </SimpleGrid>
    )

  return (
    <Box
      h="100%"
      className={osDrag && !trashView ? 'os-drop' : undefined}
      style={{ overflow: 'auto', position: 'relative' }}
      onClick={() => {
        setSelection(new Set())
        setMenu(null)
      }}
      onContextMenu={(e) => contextMenu(e, null)}
      onDragOver={(e) => {
        e.preventDefault()
        if (!trashView && !e.dataTransfer.types.includes('application/x-tup')) setOsDrag(true)
      }}
      onDragLeave={(e) => {
        if (e.currentTarget === e.target) setOsDrag(false)
      }}
      onDrop={(e) => void handleDrop(e)}
    >
      {trashView && (
        <Group
          justify="space-between"
          px="md"
          py={8}
          style={{ borderBottom: '1px solid var(--mantine-color-dark-4)' }}
        >
          <Text size="sm" c="dimmed">
            Deleted files. Restore them, or empty the bin to delete files and all their versions
            forever.
          </Text>
          <Button
            color="red"
            variant="light"
            size="compact-xs"
            disabled={rows.length === 0}
            onClick={() => void emptyBin()}
          >
            Empty Recycle Bin
          </Button>
        </Group>
      )}
      {loading && (
        <Center py="xl">
          <Loader size="sm" />
        </Center>
      )}
      {rows.length === 0 && !loading ? (
        <Stack align="center" py={80} gap={4}>
          <Text fw={600}>{trashView ? 'The Recycle Bin is empty.' : 'This folder is empty.'}</Text>
          <Text c="dimmed" size="sm">
            {trashView
              ? 'Deleted files land here until you empty the bin.'
              : 'Drop files here to upload them to this Telegram chat.'}
          </Text>
        </Stack>
      ) : (
        body
      )}
      {osDrag && !trashView && (
        <Box className="drop-overlay">
          <Text fw={600}>
            Drop to upload to <Text span ff="monospace">{currentDir}</Text>
          </Text>
        </Box>
      )}
      {menu && <ContextMenu menu={menu} close={() => setMenu(null)} trashView={trashView} />}
    </Box>
  )
}

/** Telegram thumbnail when one exists; otherwise the file type's icon. */
function GridThumb({ row }: { row: Row }) {
  const [failed, setFailed] = useState(false)
  const isMedia =
    row.kind === 'file' && (row.mediaKind === 'photo' || row.mediaKind === 'video') && row.entry
  if (isMedia && !failed) {
    return (
      <Image
        src={thumbUrl(row.entry!.id)}
        h={72}
        radius="sm"
        fit="cover"
        loading="lazy"
        onError={() => setFailed(true)}
      />
    )
  }
  return (
    <Center h={72}>
      {row.kind === 'folder' ? (
        <KindIcon kind="folder" size={40} />
      ) : (
        <FileIcon name={row.name} mediaKind={row.mediaKind} size={40} />
      )}
    </Center>
  )
}

function ContextMenu({
  menu,
  close,
  trashView,
}: {
  menu: MenuState
  close: () => void
  trashView: boolean
}) {
  const clipboard = useStore((s) => s.clipboard)
  const setPreview = useStore((s) => s.setPreview)
  const setEditorTarget = useStore((s) => s.setEditorTarget)
  const selection = useStore((s) => s.selection)
  const toast = useStore((s) => s.toast)
  const row = menu.row

  const items: React.ReactNode[] = []
  const add = (label: string, action: () => void, disabled = false) =>
    items.push(
      <Menu.Item key={label} disabled={disabled} onClick={action}>
        {label}
      </Menu.Item>,
    )

  if (trashView) {
    if (row && row.kind === 'file' && row.entry) {
      const entry = row.entry
      add('Restore', () => void restoreRows(selection.has(row.name) ? [...selection] : [row.name]))
      add('Open preview', () => setPreview(entry))
      items.push(<Menu.Divider key="d" />)
      add('Delete forever', () => void deleteSelection())
    } else if (row && row.kind === 'folder') {
      add('Open', () => openRow(row))
    } else {
      add('Refresh', () => void useStore.getState().refreshIndex())
    }
  } else if (row) {
    if (row.kind === 'file' && row.entry) {
      const entry = row.entry
      if (isOffice(entry.file_name)) {
        add('Edit document', () => useStore.getState().setOfficeEntry(entry))
        add('Export as PDF', () => window.open(exportUrl(entry.id, 'pdf'), '_blank'))
      } else {
        add('Open preview', () => setPreview(entry))
      }
      if (isMarkdown(entry.file_name)) {
        add('Edit markdown', () => setEditorTarget({ entry }))
        add('Export as PDF', () => window.open(exportUrl(entry.id, 'pdf'), '_blank'))
      }
      add('Download', () => window.open(downloadUrl(entry.id), '_blank'))
      if (entry.media_kind === 'video' || entry.media_kind === 'audio')
        add('Cache for fast playback', async () => {
          try {
            await api.warmCache(entry.id)
            toast('info', 'Caching on the server — seeking gets faster shortly.')
          } catch (error) {
            toast('error', (error as Error).message)
          }
        })
      items.push(<Menu.Divider key="d1" />)
      add('Copy', () => copySelection('copy'))
      add('Cut', () => copySelection('cut'))
      items.push(<Menu.Divider key="d2" />)
    }
    add(row.kind === 'folder' ? 'Delete folder' : 'Move to Recycle Bin', () =>
      void deleteSelection(),
    )
  } else {
    add('New folder', () => void newFolder())
    add('New markdown file', () =>
      setEditorTarget({ newIn: useStore.getState().currentDir }),
    )
    add('New document', () => void createOfficeFile('document'))
    add('New spreadsheet', () => void createOfficeFile('spreadsheet'))
    add('New presentation', () => void createOfficeFile('presentation'))
    add('New drawing', () => void createOfficeFile('drawing'))
    add('Paste', () => void paste(), clipboard === null)
    add('Refresh', () => void useStore.getState().refreshIndex())
  }

  return (
    <Menu opened onClose={close} position="bottom-start" shadow="md" withinPortal>
      <Menu.Target>
        <div style={{ position: 'fixed', left: menu.x, top: menu.y, width: 1, height: 1 }} />
      </Menu.Target>
      <Menu.Dropdown onClick={close}>{items}</Menu.Dropdown>
    </Menu>
  )
}
