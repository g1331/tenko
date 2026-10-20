import { ref, watchEffect } from 'vue'

export type ThemePreference = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'tenko-webui-theme'
const media = matchMedia('(prefers-color-scheme: dark)')
const stored = localStorage.getItem(STORAGE_KEY)

const preference = ref<ThemePreference>(
  stored === 'dark' || stored === 'system' || stored === 'light' ? stored : 'light',
)
const systemDark = ref(media.matches)
media.addEventListener('change', (event) => (systemDark.value = event.matches))

/** 按偏好给 <html> 加 / 去 `dark` 类（Element Plus 暗色变量的开关）；在 main.ts 调用一次。 */
export function installTheme(): void {
  watchEffect(() => {
    const dark = preference.value === 'dark' || (preference.value === 'system' && systemDark.value)
    document.documentElement.classList.toggle('dark', dark)
  })
}

export function useTheme() {
  function setTheme(value: ThemePreference) {
    preference.value = value
    localStorage.setItem(STORAGE_KEY, value)
  }
  return { preference, setTheme }
}
