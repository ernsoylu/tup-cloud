import {
  Alert,
  Anchor,
  Button,
  Center,
  Code,
  Paper,
  PinInput,
  Stack,
  Tabs,
  Text,
  TextInput,
  Title,
} from '@mantine/core'
import { FormEvent, useEffect, useState } from 'react'
import { api } from '../api'
import { useStore } from '../store'

export default function Login() {
  const setUser = useStore((s) => s.setUser)
  const bootstrap = useStore((s) => s.bootstrap)
  const [botName, setBotName] = useState('')
  const [identifier, setIdentifier] = useState('')
  const [challenge, setChallenge] = useState<string | null>(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api
      .botInfo()
      .then((r) => setBotName(r.bot))
      .catch(() => setBotName(''))
  }, [])

  const finishLogin = async (verifyChallenge: string | null) => {
    setError('')
    setBusy(true)
    try {
      const user = await api.verifyCode(verifyChallenge, code.trim())
      setUser(user)
      await bootstrap()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const requestCode = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      const result = await api.requestCode(identifier)
      setChallenge(result.challenge)
      if (result.bot) setBotName(result.bot)
      setCode('')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const botLabel = botName ? `@${botName}` : 'the bot'

  return (
    <Center h="100vh">
      <Paper withBorder shadow="md" p="xl" radius="md" w={400}>
        <Title order={2}>☁️ tup-cloud</Title>
        <Text c="dimmed" size="sm" mb="md">
          Telegram drives in your browser
        </Text>

        <Tabs defaultValue="bot" onChange={() => setError('')}>
          <Tabs.List grow>
            <Tabs.Tab value="bot">Code from bot</Tabs.Tab>
            <Tabs.Tab value="web">Send me a code</Tabs.Tab>
          </Tabs.List>

          <Tabs.Panel value="bot" pt="md">
            <form
              onSubmit={(e) => {
                e.preventDefault()
                void finishLogin(null)
              }}
            >
              <Stack gap="sm">
                <Text size="sm" c="dimmed">
                  1. Open{' '}
                  {botName ? (
                    <Anchor href={`https://t.me/${botName}`} target="_blank">
                      @{botName}
                    </Anchor>
                  ) : (
                    'the bot'
                  )}{' '}
                  in Telegram (press <b>Start</b> the first time).
                  <br />
                  2. Send <Code>/login</Code> — it replies with a one-time code.
                  <br />
                  3. Enter the code below.
                </Text>
                <TextInput
                  label="One-time code"
                  placeholder="e.g. K7PT29XQ"
                  value={code}
                  onChange={(e) => setCode(e.currentTarget.value.toUpperCase())}
                  autoFocus
                  disabled={busy}
                />
                <Button type="submit" loading={busy} disabled={code.trim().length < 4}>
                  Log in
                </Button>
              </Stack>
            </form>
          </Tabs.Panel>

          <Tabs.Panel value="web" pt="md">
            {challenge === null ? (
              <form onSubmit={requestCode}>
                <Stack gap="sm">
                  <TextInput
                    label="Telegram @username or numeric ID"
                    placeholder="@username or 123456789"
                    value={identifier}
                    onChange={(e) => setIdentifier(e.currentTarget.value)}
                    disabled={busy}
                  />
                  <Button type="submit" loading={busy} disabled={identifier.trim().length < 2}>
                    Send login code
                  </Button>
                  <Text size="xs" c="dimmed">
                    Works when {botLabel} can already message you — you must be a member of a drive
                    chat and have started the bot before. Otherwise use the <b>Code from bot</b>{' '}
                    tab.
                  </Text>
                </Stack>
              </form>
            ) : (
              <form
                onSubmit={(e) => {
                  e.preventDefault()
                  void finishLogin(challenge)
                }}
              >
                <Stack gap="sm" align="center">
                  <Text size="sm">Code sent via Telegram by {botLabel}</Text>
                  <PinInput
                    length={6}
                    type="number"
                    oneTimeCode
                    autoFocus
                    value={code}
                    onChange={setCode}
                    disabled={busy}
                  />
                  <Button type="submit" loading={busy} disabled={code.trim().length < 6} fullWidth>
                    Log in
                  </Button>
                  <Button variant="subtle" size="xs" onClick={() => setChallenge(null)}>
                    Use a different account
                  </Button>
                </Stack>
              </form>
            )}
          </Tabs.Panel>
        </Tabs>

        {error && (
          <Alert color="red" mt="md" p="xs">
            {error}
          </Alert>
        )}
      </Paper>
    </Center>
  )
}
