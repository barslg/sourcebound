import js from '@eslint/js'
import globals from 'globals'
import tseslint from 'typescript-eslint'
import vue from 'eslint-plugin-vue'

const bans = [
  { selector: "VAttribute[directive=true][key.name.name='html']", message: 'v-html is banned.' },
  { selector: "VAttribute[directive=false][key.name='style']", message: 'Inline styles are banned; use tokens and classes.' },
  { selector: "VAttribute[directive=true][key.argument.name='style']", message: 'Inline styles are banned; use tokens and classes.' },
  { selector: "CallExpression[callee.name='eval']", message: 'eval is banned.' },
  { selector: "Property[key.name='innerHTML']", message: 'innerHTML is banned.' },
  { selector: "AssignmentExpression[left.property.name='innerHTML']", message: 'innerHTML is banned.' },
]

export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**', 'test-results/**', 'playwright-report/**'] },
  js.configs.recommended,
  ...tseslint.configs.strict,
  ...vue.configs['flat/recommended'],
  {
    files: ['**/*.{ts,vue}'],
    languageOptions: { globals: { ...globals.browser, ...globals.node }, parserOptions: { parser: tseslint.parser } },
    rules: {
      'no-restricted-globals': ['error', { name: 'fetch', message: 'Call the API only through src/api/client.ts.' }, { name: 'EventSource', message: 'Use src/api/events.ts.' }],
      'no-restricted-properties': ['error', { object: 'window', property: 'fetch', message: 'Call the API only through src/api/client.ts.' }, { object: 'globalThis', property: 'fetch', message: 'Call the API only through src/api/client.ts.' }],
      'no-restricted-syntax': ['error', ...bans],
      'no-restricted-imports': ['error', { patterns: [{ group: ['axios', 'ky', 'ofetch'], message: 'One API wrapper only.' }] }],
      'vue/multi-word-component-names': 'off',
      'vue/no-v-html': 'error',
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/html-self-closing': 'off',
      'vue/html-indent': 'off',
      'vue/html-closing-bracket-newline': 'off',
    },
  },
  { files: ['src/api/**', 'worker/**', 'tests/**', 'e2e/**'], rules: { 'no-restricted-globals': 'off', 'no-restricted-properties': 'off' } },
)
