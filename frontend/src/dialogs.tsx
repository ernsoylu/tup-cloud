/** Promise-based wrappers over Mantine's modals manager, replacing native
 * window.confirm / window.prompt everywhere. */

import { Text, TextInput } from '@mantine/core'
import { modals } from '@mantine/modals'

export function confirmModal(options: {
  title: string
  message: string
  confirmLabel?: string
  danger?: boolean
}): Promise<boolean> {
  return new Promise((resolve) => {
    modals.openConfirmModal({
      title: options.title,
      centered: true,
      children: (
        // File names are long unbroken tokens; without this they overflow the
        // modal and force a horizontal scrollbar.
        <Text size="sm" style={{ overflowWrap: 'anywhere' }}>
          {options.message}
        </Text>
      ),
      labels: { confirm: options.confirmLabel ?? 'OK', cancel: 'Cancel' },
      confirmProps: options.danger ? { color: 'red' } : {},
      onConfirm: () => resolve(true),
      onClose: () => resolve(false), // resolves after onConfirm; first resolve wins
    })
  })
}

export function promptModal(options: {
  title: string
  label?: string
  placeholder?: string
  initial?: string
  confirmLabel?: string
}): Promise<string | null> {
  return new Promise((resolve) => {
    let value = options.initial ?? ''
    modals.openConfirmModal({
      title: options.title,
      centered: true,
      children: (
        <TextInput
          label={options.label}
          placeholder={options.placeholder}
          defaultValue={options.initial ?? ''}
          data-autofocus
          onChange={(e) => {
            value = e.currentTarget.value
          }}
        />
      ),
      labels: { confirm: options.confirmLabel ?? 'OK', cancel: 'Cancel' },
      onConfirm: () => resolve(value),
      onClose: () => resolve(null),
    })
  })
}
