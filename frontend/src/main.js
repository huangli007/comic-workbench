import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

import App from './App.vue'
import HomeView from './views/HomeView.vue'
import ProjectView from './views/ProjectView.vue'
import './style.css'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/project/:id', name: 'project', component: ProjectView, props: true },
  ],
})

createApp(App).use(router).use(ElementPlus, { locale: zhCn }).mount('#app')
