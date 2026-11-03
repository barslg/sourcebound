import { expect, signInAs, test } from './fixtures'

test('account: a key is added, shown by its last four characters only, and removed', async ({ page, context }) => {
  await signInAs(context, 'author')
  await page.goto('/account')
  await expect(page.getByTestId('key-status')).toContainText('No key on file')
  await page.getByTestId('key-input').fill('sk-meta-test-0000-1234-abcdef-WXYZ')
  await page.getByTestId('key-save').click()
  await expect(page.getByTestId('key-status')).toContainText('…WXYZ')
  expect(await page.content()).not.toContain('sk-meta-test-0000')
  await page.getByTestId('key-remove').click()
  await page.getByRole('dialog').getByTestId('dialog-submit').click()
  await expect(page.getByTestId('key-status')).toContainText('No key on file')
})
