import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'LingCast',
  description: '灵播 LingCast · 流式互动数字人直播平台',
  lang: 'zh-CN',
  // GitHub Pages 部署在 <user>.github.io/LingCast/
  base: '/LingCast/',
  lastUpdated: true,
  cleanUrls: true,
  // API 文档页指向本地 Swagger 网关（构建时不可达），跳过校验。
  ignoreDeadLinks: [/^https?:\/\/localhost/],
  themeConfig: {
    logo: '/images/logo.svg',
    nav: [
      { text: '首页', link: '/' },
      { text: '快速开始', link: '/guide/start' },
      { text: '已实现功能', link: '/guide/features' },
      { text: 'API 文档', link: '/guide/api' },
      { text: '开发路线', link: '/TODO' },
      { text: 'GitHub', link: 'https://github.com/taochangle/LingCast' },
    ],
    sidebar: [
      {
        text: '指南',
        items: [
          { text: '快速开始', link: '/guide/start' },
          { text: '已实现功能', link: '/guide/features' },
          { text: '架构与技术需求', link: '/技术需求与架构文档' },
          { text: '流式直播', link: '/cosyvoice-musetalk-live' },
          { text: '自主主持', link: '/autonomous-host' },
          { text: '换装与缓存', link: '/wardrobe' },
          { text: '项目速读', link: '/project-reader' },
          { text: 'Chrome采集', link: '/chrome-collector' },
          { text: '感谢与优先级', link: '/thanks-priority' },
          { text: '点播小节目', link: '/viewer-interaction' },
          { text: '防提示词注入', link: '/live-prompt-guard' },
          { text: 'API 文档（Swagger 网关）', link: '/guide/api' },
          { text: '开发路线 TODO', link: '/TODO' },
        ],
      },
    ],
    socialLinks: [{ icon: 'github', link: 'https://github.com/taochangle/LingCast' }],
    footer: {
      message: '灵播 LingCast · 端到端口播数字人平台',
    },
  },
})
