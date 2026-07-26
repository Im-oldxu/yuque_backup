<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import {
  Badge, Button, Separator, Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent,
  SidebarGroupLabel, SidebarHeader, SidebarInset, SidebarMenu, SidebarMenuButton, SidebarMenuItem,
  SidebarMenuSub, SidebarMenuSubButton, SidebarMenuSubItem, SidebarProvider, SidebarRail, SidebarTrigger, toast,
} from '@/components/ui'
import { ArchiveX, BookOpen, CalendarClock, ChevronRight, DatabaseBackup, Gauge, KeyRound, ListChecks, LogOut, Moon, Play, Settings, Sun } from 'lucide-vue-next'
import { API_MODE } from '@/api'
import { useSessionStore } from '@/stores/session'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const dark = ref(false)

const primaryNavigation = [
  { label: '仪表盘', path: '/dashboard', icon: Gauge },
  { label: '知识库', path: '/repositories', icon: BookOpen },
]

const secondaryNavigation = [
  { label: '语雀凭据', path: '/credentials', icon: KeyRound },
  { label: '删除记录', path: '/tombstones', icon: ArchiveX },
  { label: '设置', path: '/settings', icon: Settings },
]

const pageTitle = computed(() => String(route.meta.title ?? '语雀备份'))
const activePath = computed(() => String(route.meta.activePath ?? route.path))
const backupActive = computed(() => activePath.value.startsWith('/jobs/'))

onMounted(() => {
  dark.value = localStorage.getItem('yb_theme') === 'dark' || (!localStorage.getItem('yb_theme') && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', dark.value)
})

watch(() => session.isAuthenticated, (authenticated) => {
  if (!authenticated && session.bootstrapped && route.name !== 'auth') {
    void router.replace({ name: 'auth', query: { redirect: route.fullPath } })
  }
})

function toggleTheme() {
  dark.value = !dark.value
  document.documentElement.classList.toggle('dark', dark.value)
  localStorage.setItem('yb_theme', dark.value ? 'dark' : 'light')
}

async function logout() {
  try { await session.logout() } catch { toast.error('服务端未确认退出，本地会话已清除。') }
  await router.replace('/auth')
}
</script>

<template>
  <SidebarProvider>
    <Sidebar collapsible="icon" class="border-r border-sidebar-border">
      <SidebarHeader class="p-3">
        <RouterLink to="/dashboard" class="flex min-h-10 items-center gap-3 rounded-md px-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring">
          <span class="flex size-8 shrink-0 items-center justify-center rounded-md bg-sidebar-primary text-sidebar-primary-foreground"><DatabaseBackup class="size-4" /></span>
          <span class="min-w-0 group-data-[collapsible=icon]:hidden">
            <strong class="block truncate text-sm">Yuque Backup</strong>
            <span class="block truncate text-xs text-muted-foreground">本地只读备份</span>
          </span>
        </RouterLink>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>工作区</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem v-for="item in primaryNavigation" :key="item.path">
                <SidebarMenuButton as-child :is-active="activePath === item.path" :tooltip="item.label">
                  <RouterLink :to="item.path">
                    <component :is="item.icon" />
                    <span>{{ item.label }}</span>
                    <ChevronRight v-if="activePath === item.path" class="ml-auto group-data-[collapsible=icon]:hidden" />
                  </RouterLink>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton :is-active="backupActive" tooltip="备份任务">
                  <ListChecks />
                  <span>备份任务</span>
                  <ChevronRight class="ml-auto group-data-[collapsible=icon]:hidden" />
                </SidebarMenuButton>
                <SidebarMenuSub>
                  <SidebarMenuSubItem>
                    <SidebarMenuSubButton as-child :is-active="activePath === '/jobs/manual'">
                      <RouterLink to="/jobs/manual"><Play /><span>手动备份</span></RouterLink>
                    </SidebarMenuSubButton>
                  </SidebarMenuSubItem>
                  <SidebarMenuSubItem>
                    <SidebarMenuSubButton as-child :is-active="activePath === '/jobs/scheduled'">
                      <RouterLink to="/jobs/scheduled"><CalendarClock /><span>定时备份</span></RouterLink>
                    </SidebarMenuSubButton>
                  </SidebarMenuSubItem>
                </SidebarMenuSub>
              </SidebarMenuItem>
              <SidebarMenuItem v-for="item in secondaryNavigation" :key="item.path">
                <SidebarMenuButton as-child :is-active="activePath === item.path" :tooltip="item.label">
                  <RouterLink :to="item.path">
                    <component :is="item.icon" />
                    <span>{{ item.label }}</span>
                    <ChevronRight v-if="activePath === item.path" class="ml-auto group-data-[collapsible=icon]:hidden" />
                  </RouterLink>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter class="p-3">
        <div class="flex items-center gap-2 rounded-md border border-sidebar-border bg-sidebar-accent/35 p-2 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:border-0 group-data-[collapsible=icon]:bg-transparent group-data-[collapsible=icon]:p-0">
          <div class="min-w-0 flex-1 group-data-[collapsible=icon]:hidden">
            <p class="truncate text-sm font-medium">{{ session.administrator?.username }}</p>
            <p class="text-xs text-muted-foreground">本地管理员</p>
          </div>
          <Button variant="ghost" size="icon" title="退出登录" aria-label="退出登录" @click="logout"><LogOut /></Button>
        </div>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
    <SidebarInset class="min-w-0 bg-background">
      <header class="sticky top-0 z-10 flex h-14 items-center gap-3 border-b bg-background/95 px-4 backdrop-blur">
        <SidebarTrigger />
        <Separator orientation="vertical" class="h-4" />
        <h2 class="min-w-0 flex-1 truncate text-sm font-medium">{{ pageTitle }}</h2>
        <Badge v-if="API_MODE === 'mock'" variant="outline">Mock</Badge>
        <Button variant="ghost" size="icon" :title="dark ? '切换到浅色模式' : '切换到深色模式'" :aria-label="dark ? '切换到浅色模式' : '切换到深色模式'" @click="toggleTheme">
          <Sun v-if="dark" />
          <Moon v-else />
        </Button>
      </header>
      <main class="min-w-0 flex-1 overflow-x-hidden"><RouterView /></main>
    </SidebarInset>
  </SidebarProvider>
</template>
