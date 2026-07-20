import {
  ActionIcon,
  AppShell,
  Badge,
  Button,
  Group,
  Modal,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core'
import {
  IconArrowUp,
  IconCloud,
  IconEye,
  IconFilePlus,
  IconFileSpreadsheet,
  IconFileText,
  IconFolderPlus,
  IconLayoutGrid,
  IconList,
  IconListTree,
  IconMarkdown,
  IconPresentation,
  IconRefresh,
  IconVector,
  IconSettings,
  IconUpload,
} from '@tabler/icons-react'
import { ChangeEvent, useCallback, useMemo, useRef, useState } from 'react'
import { Menu } from '@mantine/core'
import { api } from '../api'
import { confirmModal } from '../dialogs'
import { useShortcuts } from '../hooks/useShortcuts'
import { createOfficeFile, newFolder } from '../ops'
import { parentDir, useStore } from '../store'
import type { AdminUserRow } from '../types'
import Breadcrumbs from './Breadcrumbs'
import FileList from './FileList'
import MarkdownEditor from './MarkdownEditor'
import ObserverFeed from './ObserverFeed'
import OfficeEditor from './OfficeEditor'
import Preview from './Preview'
import Sidebar from './Sidebar'
import Transfers from './Transfers'

export default function Explorer() {
  const user = useStore((s) => s.user)
  const drives = useStore((s) => s.drives)
  const currentDrive = useStore((s) => s.currentDrive)
  const selectDrive = useStore((s) => s.selectDrive)
  const currentDir = useStore((s) => s.currentDir)
  const setDir = useStore((s) => s.setDir)
  const filter = useStore((s) => s.filter)
  const setFilter = useStore((s) => s.setFilter)
  const view = useStore((s) => s.view)
  const setView = useStore((s) => s.setView)
  const recursive = useStore((s) => s.recursive)
  const toggleRecursive = useStore((s) => s.toggleRecursive)
  const refreshIndex = useStore((s) => s.refreshIndex)
  const uploadFiles = useStore((s) => s.uploadFiles)
  const transfers = useStore((s) => s.transfers)
  const showTransfers = useStore((s) => s.showTransfers)
  const showObserver = useStore((s) => s.showObserver)
  const toggleObserver = useStore((s) => s.toggleObserver)
  const logout = useStore((s) => s.logout)

  const fileInput = useRef<HTMLInputElement>(null)
  const openUploadPicker = useCallback(() => fileInput.current?.click(), [])
  useShortcuts(openUploadPicker)

  const [showAdmin, setShowAdmin] = useState(false)
  const hasTransfers = Object.keys(transfers).length > 0
  const transfersVisible = showTransfers && hasTransfers

  const driveOptions = useMemo(
    () => drives.map((d) => ({ value: d.chat_id, label: `${d.alias} — ${d.title}` })),
    [drives],
  )

  const onPick = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    event.target.value = ''
    if (files.length)
      await uploadFiles(
        files.filter((f) => !f.name.startsWith('.')).map((file) => ({ file, relDir: '' })),
      )
  }

  return (
    <AppShell
      header={{ height: 52 }}
      navbar={{ width: 240, breakpoint: 'xs' }}
      aside={{ width: 320, breakpoint: 'xs', collapsed: { desktop: !showObserver, mobile: true } }}
      footer={transfersVisible ? { height: 165 } : undefined}
      padding={0}
    >
      <AppShell.Header>
        <Group h="100%" px="sm" gap="xs" wrap="nowrap">
          <Group gap={6} wrap="nowrap">
            <IconCloud size={20} color="var(--mantine-color-blue-4)" />
            <Title order={5} style={{ whiteSpace: 'nowrap' }}>
              tup-cloud
            </Title>
          </Group>
          <Select
            data={driveOptions}
            value={currentDrive}
            onChange={(v) => v && selectDrive(v)}
            placeholder="No drives"
            w={210}
            size="xs"
            allowDeselect={false}
          />
          <Tooltip label="Up one folder (Backspace)">
            <ActionIcon
              variant="default"
              disabled={currentDir === '/'}
              onClick={() => setDir(parentDir(currentDir))}
            >
              <IconArrowUp size={16} />
            </ActionIcon>
          </Tooltip>
          <Breadcrumbs />
          <Tooltip label="Refresh (F5)">
            <ActionIcon variant="default" onClick={() => void refreshIndex()}>
              <IconRefresh size={16} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="New folder (Ctrl+Shift+N)">
            <ActionIcon variant="default" onClick={() => void newFolder()}>
              <IconFolderPlus size={16} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="New markdown file">
            <ActionIcon
              variant="default"
              onClick={() => useStore.getState().setEditorTarget({ newIn: currentDir })}
            >
              <IconMarkdown size={16} />
            </ActionIcon>
          </Tooltip>
          <Menu position="bottom-start" shadow="md">
            <Menu.Target>
              <Tooltip label="New office document">
                <ActionIcon variant="default">
                  <IconFilePlus size={16} />
                </ActionIcon>
              </Tooltip>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Item
                leftSection={<IconFileText size={15} />}
                onClick={() => void createOfficeFile('document')}
              >
                New document (.docx)
              </Menu.Item>
              <Menu.Item
                leftSection={<IconFileSpreadsheet size={15} />}
                onClick={() => void createOfficeFile('spreadsheet')}
              >
                New spreadsheet (.xlsx)
              </Menu.Item>
              <Menu.Item
                leftSection={<IconPresentation size={15} />}
                onClick={() => void createOfficeFile('presentation')}
              >
                New presentation (.pptx)
              </Menu.Item>
              <Menu.Item
                leftSection={<IconVector size={15} />}
                onClick={() => void createOfficeFile('drawing')}
              >
                New drawing (.odg)
              </Menu.Item>
            </Menu.Dropdown>
          </Menu>
          <Button size="xs" leftSection={<IconUpload size={14} />} onClick={openUploadPicker}>
            Upload
          </Button>
          <input ref={fileInput} type="file" multiple hidden onChange={(e) => void onPick(e)} />
          <TextInput
            id="file-filter"
            placeholder="Filter or #tag  ( / )"
            size="xs"
            w={170}
            value={filter}
            onChange={(e) => setFilter(e.currentTarget.value)}
          />
          <Tooltip label={view === 'details' ? 'Grid view (G)' : 'List view (G)'}>
            <ActionIcon
              variant="default"
              onClick={() => setView(view === 'details' ? 'grid' : 'details')}
            >
              {view === 'details' ? <IconLayoutGrid size={16} /> : <IconList size={16} />}
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Recursive listing (R)">
            <ActionIcon
              variant={recursive ? 'light' : 'default'}
              color={recursive ? 'blue' : undefined}
              onClick={toggleRecursive}
            >
              <IconListTree size={16} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Observer feed (O)">
            <ActionIcon
              variant={showObserver ? 'light' : 'default'}
              color={showObserver ? 'blue' : undefined}
              onClick={toggleObserver}
            >
              <IconEye size={16} />
            </ActionIcon>
          </Tooltip>
          {user?.role === 'admin' && (
            <Tooltip label="Administration">
              <ActionIcon variant="default" onClick={() => setShowAdmin(true)}>
                <IconSettings size={16} />
              </ActionIcon>
            </Tooltip>
          )}
          <div style={{ flex: 1 }} />
          <Text size="xs" c="dimmed" ff="monospace" style={{ whiteSpace: 'nowrap' }}>
            {user?.username ? `@${user.username}` : user?.telegram_id}
          </Text>
          <Button variant="default" size="xs" onClick={() => void logout()}>
            Log out
          </Button>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar>
        <Sidebar />
      </AppShell.Navbar>

      <AppShell.Aside>
        <ObserverFeed />
      </AppShell.Aside>

      <AppShell.Main>
        <div
          style={{
            height:
              'calc(100dvh - var(--app-shell-header-height, 0px) - var(--app-shell-footer-height, 0px))',
          }}
        >
          <FileList />
        </div>
      </AppShell.Main>

      {transfersVisible && (
        <AppShell.Footer>
          <Transfers />
        </AppShell.Footer>
      )}

      <Preview />
      <MarkdownEditor />
      <OfficeEditor />
      {showAdmin && <AdminPanel close={() => setShowAdmin(false)} />}
    </AppShell>
  )
}

function AdminPanel({ close }: { close: () => void }) {
  const drives = useStore((s) => s.drives)
  const loadDrives = useStore((s) => s.loadDrives)
  const toast = useStore((s) => s.toast)
  const [users, setUsers] = useState<AdminUserRow[] | null>(null)
  const [alias, setAlias] = useState('')
  const [chatId, setChatId] = useState('')

  const loadUsers = async () => {
    try {
      setUsers(await api.listUsers())
    } catch (error) {
      toast('error', (error as Error).message)
    }
  }

  return (
    <Modal opened onClose={close} title="Administration" size="lg">
      <Stack gap="lg">
        <div>
          <Text fw={600} mb="xs">
            Drives
          </Text>
          <Table verticalSpacing={4}>
            <Table.Tbody>
              {drives.map((d) => (
                <Table.Tr key={d.chat_id}>
                  <Table.Td>
                    <Text size="sm" fw={600} span>
                      {d.alias}
                    </Text>{' '}
                    <Text size="sm" span>
                      — {d.title}
                    </Text>{' '}
                    <Text size="xs" c="dimmed" ff="monospace" span>
                      {d.chat_id}
                    </Text>
                  </Table.Td>
                  <Table.Td w={90} ta="right">
                    <Button
                      variant="subtle"
                      color="red"
                      size="compact-xs"
                      onClick={async () => {
                        const confirmed = await confirmModal({
                          title: 'Remove drive',
                          message: `Remove drive "${d.alias}"? Files stay on Telegram; only the drive registration is removed.`,
                          confirmLabel: 'Remove',
                          danger: true,
                        })
                        if (!confirmed) return
                        try {
                          await api.removeDrive(d.alias)
                          await loadDrives()
                        } catch (error) {
                          toast('error', (error as Error).message)
                        }
                      }}
                    >
                      Remove
                    </Button>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          <Group gap="xs" mt="xs">
            <TextInput
              placeholder="alias"
              size="xs"
              w={110}
              value={alias}
              onChange={(e) => setAlias(e.currentTarget.value)}
            />
            <TextInput
              placeholder="chat id, e.g. -1001234567890"
              size="xs"
              style={{ flex: 1 }}
              value={chatId}
              onChange={(e) => setChatId(e.currentTarget.value)}
            />
            <Button
              size="xs"
              disabled={!alias.trim() || !chatId.trim()}
              onClick={async () => {
                try {
                  await api.addDrive(alias.trim(), chatId.trim())
                  setAlias('')
                  setChatId('')
                  await loadDrives()
                } catch (error) {
                  toast('error', (error as Error).message)
                }
              }}
            >
              Add drive
            </Button>
          </Group>
        </div>

        <div>
          <Group gap="xs" mb="xs">
            <Text fw={600}>Users</Text>
            <Button variant="subtle" size="compact-xs" onClick={() => void loadUsers()}>
              {users ? 'Reload' : 'Load'}
            </Button>
          </Group>
          {users && (
            <Table verticalSpacing={4}>
              <Table.Tbody>
                {users.map((u) => (
                  <Table.Tr key={u.id}>
                    <Table.Td>
                      <Text size="sm" span>
                        {u.username ? `@${u.username}` : u.telegram_id}
                      </Text>{' '}
                      {u.display_name && (
                        <Text size="sm" c="dimmed" span>
                          {u.display_name}
                        </Text>
                      )}{' '}
                      <Badge variant="light" size="xs" tt="none">
                        {u.role}
                      </Badge>{' '}
                      {!u.approved && (
                        <Badge variant="light" color="red" size="xs" tt="none">
                          blocked
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td w={90} ta="right">
                      {u.role !== 'admin' && (
                        <Button
                          variant="subtle"
                          size="compact-xs"
                          color={u.approved ? 'red' : 'green'}
                          onClick={async () => {
                            try {
                              await api.toggleBlock(u.id)
                              await loadUsers()
                            } catch (error) {
                              toast('error', (error as Error).message)
                            }
                          }}
                        >
                          {u.approved ? 'Block' : 'Unblock'}
                        </Button>
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </div>
      </Stack>
    </Modal>
  )
}
