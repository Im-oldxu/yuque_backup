import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'

import './assets/css/app.css'
import './assets/css/theme.css'
import './styles/app.css'

const storedTheme = localStorage.getItem('yb_theme')
const dark = storedTheme === 'dark' || (!storedTheme && window.matchMedia('(prefers-color-scheme: dark)').matches)
document.documentElement.classList.toggle('dark', dark)

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
