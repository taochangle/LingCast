import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

type Result = {
  config: {
    url: string
    enabled: boolean
    welcome: boolean
    reply: boolean
    thanks?: boolean
    revision: number
    client?: string
  }
  status: {
    state: string
    message?: string
    received?: number
    updated_ms?: number
    browser_mode?: string
  } | null
  events: {
    id: string
    kind: string
    nickname: string
    text: string
    observed_ms: number
    action: string
  }[]
}
function message(error: unknown) {
  return (
    (error as { response?: { data?: { error?: string } } }).response?.data
      ?.error || '请求失败，请稍后重试'
  )
}
export function XHSListener({ avatarId }: { avatarId: number }) {
  const [data, setData] = useState<Result | null>(null)
  const [token, setToken] = useState('REDACTED_COLLECTOR_KEY')
  const [checkedAt, setCheckedAt] = useState(0)
  const [expires, setExpires] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const tokenInput = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (!Number.isFinite(avatarId) || avatarId <= 0) return
    const abort = new AbortController()
    const refresh = async () => {
      try {
        const response = await api.get<Result>(`/live/${avatarId}/xhs`, {
          signal: abort.signal,
        })
        if (!abort.signal.aborted) {
          setData(response.data)
          setCheckedAt(Date.now())
          setError('')
        }
      } catch (error) {
        if (!abort.signal.aborted) setError(message(error))
      }
    }
    void refresh()
    const timer = setInterval(() => void refresh(), 2500)
    return () => {
      abort.abort()
      clearInterval(timer)
    }
  }, [avatarId])
  const act = async (action: 'pair' | 'revoke' | 'stop') => {
    setBusy(true)
    setError('')
    try {
      if (action === 'pair') {
        const { data } = await api.post<{ token: string; expires_ms: number }>(
          `/live/${avatarId}/collector-token`
        )
        setToken(data.token)
        setExpires(data.expires_ms)
        toast.success('默认采集密钥已启用')
      } else if (action === 'revoke') {
        await api.delete(`/live/${avatarId}/collector-token`)
        setToken('')
        toast.success('采集密钥已撤销')
      } else if (data) {
        await api.put(`/live/${avatarId}/xhs`, {
          ...data.config,
          enabled: false,
        })
        toast.success('已停止采集和自动互动')
      }
      const result = await api.get<Result>(`/live/${avatarId}/xhs`)
      setData(result.data)
    } catch (error) {
      setError(message(error))
    } finally {
      setBusy(false)
    }
  }
  const copy = async () => {
    tokenInput.current?.select()
    try {
      if (navigator.clipboard) await navigator.clipboard.writeText(token)
      else if (!document.execCommand('copy')) throw new Error()
      toast.success('采集密钥已复制')
    } catch {
      toast.info('密钥已选中，请按 Ctrl+C 或 ⌘C 复制')
    }
  }
  const connected =
    data?.config.enabled &&
    data.config.client === 'extension' &&
    data.status?.state === 'listening' &&
    !!data.status.updated_ms &&
    checkedAt - data.status.updated_ms < 90000
  return (
    <Card>
      <CardHeader>
        <CardTitle>直播互动 · Chrome 插件</CardTitle>
        <CardDescription>
          在你正常打开的直播页面读取新聊天和进场提示，发送到当前数字人。无需启动浏览器采集服务。
        </CardDescription>
      </CardHeader>
      <CardContent className='flex flex-col gap-4'>
        <ol className='list-decimal pl-5 text-sm'>
          <li>
            下载并解压插件，在 chrome://extensions
            开启开发者模式，选择「加载已解压的扩展程序」。
          </li>
          <li>服务器和密钥已默认填写，插件数字人 ID 请设为 {avatarId}。</li>
          <li>
            手动打开自己的小红书直播页，点击插件「在当前直播页开始」。默认只采集；确认信息后再开启自动欢迎或回复。
          </li>
        </ol>
        <div className='flex flex-wrap items-center gap-2'>
          <Button variant='outline' asChild>
            <a href='/downloads/lingcast-collector.zip' download>
              下载 Chrome 插件
            </a>
          </Button>
          <Button disabled={busy} onClick={() => void act('pair')}>
            启用默认采集密钥
          </Button>
          <Button
            variant='outline'
            disabled={busy || !data?.config.enabled}
            onClick={() => void act('stop')}
          >
            停止采集
          </Button>
          <Button
            variant='outline'
            disabled={busy}
            onClick={() => void act('revoke')}
          >
            停用采集密钥
          </Button>
        </div>
        <div className='flex flex-col gap-2'>
          <Label htmlFor='collector-server'>插件固定的服务器地址</Label>
          <Input
            id='collector-server'
            readOnly
            value='http://127.0.0.1:59480'
            onFocus={(event) => event.target.select()}
          />
        </div>
        {token && (
          <div className='flex flex-col gap-2'>
            <Label htmlFor='collector-token'>当前数字人的采集密钥</Label>
            <Input
              id='collector-token'
              ref={tokenInput}
              readOnly
              value={token}
              onFocus={(event) => event.target.select()}
            />
            <div>
              <Button variant='outline' onClick={() => void copy()}>
                复制密钥
              </Button>
            </div>
            <p className='text-xs text-muted-foreground'>
              {expires ? `有效至 ${new Date(expires).toLocaleString()}` : '默认密钥长期有效'}
              ；插件按数字人 ID 绑定采集。停用后需在这里重新启用，管理员登录密码不受影响。
            </p>
          </div>
        )}
        <div className='flex flex-wrap items-center gap-2'>
          <Badge variant='secondary'>
            {connected
              ? '插件已连接'
              : data?.config.enabled
                ? '等待插件连接'
                : '未开启采集'}
          </Badge>
          <span className='text-sm text-muted-foreground'>
            已接收 {data?.status?.received || 0} 条 · 自动欢迎
            {data?.config.welcome ? '开' : '关'} · 自动回复
            {data?.config.reply ? '开' : '关'} · 自动感谢
            {data?.config.thanks ? '开' : '关'}
          </span>
        </div>
        {data?.config.client === 'extension' && data.config.url && (
          <p className='text-xs break-all text-muted-foreground'>
            当前房间：{data.config.url}
          </p>
        )}
        <p className='text-xs text-muted-foreground'>
          跳过启动时已有消息、主播发言和系统通知；重复进场去重，每分钟最多欢迎一次，每12秒最多回复一条。手动口播优先，播完继续主持。刷新直播页后需重新开始采集。
        </p>
        {error && (
          <Alert variant='destructive'>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <details open>
          <summary className='cursor-pointer text-sm'>
            最近采集消息（当前房间最多显示 40 条）
          </summary>
          {data?.events.length ? (
            <ul className='mt-3 flex max-h-64 flex-col gap-2 overflow-y-auto text-sm'>
              {data.events.map((event) => (
                <li key={event.id} className='break-words'>
                  <span className='text-muted-foreground'>
                    {new Date(event.observed_ms).toLocaleTimeString()} ·{' '}
                  </span>
                  <strong>{event.nickname}</strong>
                  {event.kind === 'enter'
                    ? ' 来了'
                    : event.kind === 'like'
                      ? ' 为主播点赞了'
                      : event.kind === 'gift'
                        ? ` 送出 ${event.text}`
                        : `：${event.text}`}
                  <span className='ml-2 text-xs text-muted-foreground'>
                    {event.action === 'gift'
                      ? '已排队礼物感谢'
                      : event.action === 'like'
                        ? '已排队点赞感谢'
                        : event.action === 'welcome'
                          ? '已排队欢迎'
                          : event.action === 'reply'
                            ? '回复生成中'
                            : '仅展示'}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className='mt-3 text-sm text-muted-foreground'>
              尚未收到新消息。插件启动前的历史消息不会补播。
            </p>
          )}
        </details>
      </CardContent>
    </Card>
  )
}
