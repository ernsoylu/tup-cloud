import '@mantine/core/styles.css'
import '@mantine/notifications/styles.css'
import '@mantine/tiptap/styles.css'
import './styles.css'

import { MantineProvider } from '@mantine/core'
import { ModalsProvider } from '@mantine/modals'
import { Notifications } from '@mantine/notifications'
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <MantineProvider defaultColorScheme="dark">
      <ModalsProvider>
        <Notifications position="bottom-right" />
        <App />
      </ModalsProvider>
    </MantineProvider>
  </React.StrictMode>,
)
