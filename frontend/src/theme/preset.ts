import Aura from '@primeuix/themes/aura'
import { definePreset } from '@primeuix/themes'

const v = (name: string): string => `var(--sb-${name})`
const ink = { 50: '#f4f5f8', 100: '#e4e7ee', 200: '#c8cedb', 300: '#a3adc1', 400: '#7584a0', 500: '#4d5b78', 600: '#364260', 700: '#2c3547', 800: '#232b3b', 900: '#1b2230', 950: '#111621' }

const both = {
  primary: { color: v('primary'), contrastColor: v('primary-fg'), hoverColor: v('primary-hover'), activeColor: v('primary-hover') },
  highlight: { background: v('info-bg'), focusBackground: v('info-bg'), color: v('info-fg'), focusColor: v('info-fg') },
  surface: { 0: v('surface'), 50: v('surface-2'), 100: v('surface-2'), 200: v('border'), 300: v('border'), 400: v('border-strong'), 500: v('muted'), 600: v('muted'), 700: v('fg'), 800: v('fg'), 900: v('fg'), 950: v('fg') },
  text: { color: v('fg'), hoverColor: v('fg'), mutedColor: v('muted'), hoverMutedColor: v('fg') },
  content: { background: v('surface'), hoverBackground: v('surface-2'), borderColor: v('border'), color: v('fg'), hoverColor: v('fg') },
  mask: { background: 'rgb(27 34 48 / 45%)', color: v('fg') },
  formField: { background: v('surface'), disabledBackground: v('surface-2'), filledBackground: v('surface'), filledHoverBackground: v('surface'), filledFocusBackground: v('surface'), borderColor: v('border-strong'), hoverBorderColor: v('fg'), focusBorderColor: v('focus'), invalidBorderColor: v('danger'), color: v('fg'), disabledColor: v('muted'), placeholderColor: v('muted'), invalidPlaceholderColor: v('danger'), floatLabelColor: v('muted'), floatLabelFocusColor: v('muted'), floatLabelActiveColor: v('muted'), floatLabelInvalidColor: v('danger'), iconColor: v('muted'), shadow: 'none' },
  overlay: {
    select: { background: v('surface'), borderColor: v('border'), color: v('fg') },
    popover: { background: v('surface'), borderColor: v('border'), color: v('fg') },
    modal: { background: v('surface'), borderColor: v('border'), color: v('fg') },
  },
  list: { option: { focusBackground: v('surface-2'), selectedBackground: v('info-bg'), selectedFocusBackground: v('info-bg'), color: v('fg'), focusColor: v('fg'), selectedColor: v('info-fg'), selectedFocusColor: v('info-fg'), icon: { color: v('muted'), focusColor: v('fg') } } },
  navigation: { item: { focusBackground: v('surface-2'), activeBackground: v('surface-2'), color: v('fg'), focusColor: v('fg'), activeColor: v('fg'), icon: { color: v('muted'), focusColor: v('fg'), activeColor: v('fg') } } },
}

const buttonScheme = {
  root: {
    secondary: { background: v('surface-2'), hoverBackground: v('border'), activeBackground: v('border'), borderColor: v('border-strong'), hoverBorderColor: v('border-strong'), activeBorderColor: v('border-strong'), color: v('fg'), hoverColor: v('fg'), activeColor: v('fg'), focusRing: { color: v('focus'), shadow: 'none' } },
    contrast: { background: v('primary'), hoverBackground: v('primary-hover'), activeBackground: v('primary-hover'), borderColor: v('primary'), hoverBorderColor: v('primary-hover'), activeBorderColor: v('primary-hover'), color: v('primary-fg'), hoverColor: v('primary-fg'), activeColor: v('primary-fg'), focusRing: { color: v('focus'), shadow: 'none' } },
  },
  outlined: { secondary: { hoverBackground: v('surface-2'), activeBackground: v('surface-2'), borderColor: v('border-strong'), color: v('fg') } },
  text: { secondary: { hoverBackground: v('surface-2'), activeBackground: v('surface-2'), color: v('fg') } },
}

export const preset = definePreset(Aura, {
  semantic: {
    primary: ink,
    transitionDuration: '160ms',
    focusRing: { width: '2px', style: 'solid', color: v('focus'), offset: '2px', shadow: 'none' },
    formField: { borderRadius: '10px', paddingX: '0.85rem', paddingY: '0.6rem', focusRing: { width: '2px', style: 'solid', color: v('focus'), offset: '2px', shadow: 'none' } },
    overlay: { select: { borderRadius: '12px', shadow: v('shadow-2') }, popover: { borderRadius: '14px', shadow: v('shadow-2') }, modal: { borderRadius: '14px', shadow: v('shadow-2') }, navigation: { shadow: v('shadow-2') } },
    colorScheme: { light: both, dark: both },
  },
  components: {
    button: {
      root: { borderRadius: '10px', paddingX: '1rem', paddingY: '0.6rem', label: { fontWeight: '500' } },
      colorScheme: { light: buttonScheme, dark: buttonScheme },
    },
    dialog: { root: { borderRadius: '14px', background: v('surface'), borderColor: v('border'), color: v('fg') }, title: { fontSize: '1.5rem', fontWeight: '500' } },
    drawer: { root: { background: v('surface'), borderColor: v('border'), color: v('fg') }, title: { fontSize: '1.5rem', fontWeight: '500' } },
    datatable: { headerCell: { background: v('surface'), color: v('muted'), borderColor: v('border') }, bodyCell: { borderColor: v('border') }, row: { background: v('surface'), hoverBackground: v('surface-2'), color: v('fg'), hoverColor: v('fg') } },
    toast: { root: { borderRadius: '14px' } },
  },
})
