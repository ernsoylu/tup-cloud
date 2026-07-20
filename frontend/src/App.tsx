import { Center, Loader } from '@mantine/core'
import { useEffect } from 'react'
import Explorer from './components/Explorer'
import Login from './components/Login'
import { useStore } from './store'
import { connectWs, disconnectWs } from './ws'

export default function App() {
  const user = useStore((s) => s.user)
  const authChecked = useStore((s) => s.authChecked)
  const bootstrap = useStore((s) => s.bootstrap)

  useEffect(() => {
    void bootstrap()
  }, [bootstrap])

  useEffect(() => {
    if (user) connectWs()
    else disconnectWs()
  }, [user])

  if (!authChecked)
    return (
      <Center h="100vh">
        <Loader />
      </Center>
    )

  return user ? <Explorer /> : <Login />
}
