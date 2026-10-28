import { useEffect, useState } from 'react'
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
import { Textarea } from '@/components/ui/textarea'

type HostState = {
  config: { run_id?: string; mode?: string; scripts?: string[]; kind?: string }
  library: string[]
  status: {
    phase: string
    completed: number
    total: number
    prepared: number
    error?: string
  } | null
}
const phases: Record<string, string> = {
  preparing: '正在准备素材',
  waiting: '自然停顿，等待下一段',
  speaking: '正在主持',
  waiting_manual: '优先处理手动口播',
  paused: '已暂停',
  stopped: '已结束',
  finished: '本轮讲完',
  failed: '生成失败',
}
function message(error: unknown) {
  return (
    (error as { response?: { data?: { error?: string } } }).response?.data
      ?.error || '请求失败，请稍后重试'
  )
}
export function HostPanel({
  avatarId,
  started,
  material,
}: {
  avatarId: number
  started: boolean
  material: { topic: string; text: string } | null
}) {
  const [topic, setTopic] = useState(
    material?.topic || '欢迎朋友们，轻松聊聊日常生活'
  )
  const [source, setSource] = useState(material?.text || '')
  const [script, setScript] = useState('')
  const [minPause, setMinPause] = useState(10)
  const [maxPause, setMaxPause] = useState(25)
  const [state, setState] = useState<HostState | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    const abort = new AbortController()
    const refresh = async () => {
      try {
        const { data } = await api.get<HostState>(`/live/${avatarId}/host`, {
          signal: abort.signal,
        })
        if (!abort.signal.aborted) {
          setState(data)
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
  const paragraphs = script
    .split(/\n\s*\n/)
    .map((s) => s.trim())
    .filter(Boolean)
  const valid =
    paragraphs.length > 0 &&
    paragraphs.length <= 12 &&
    paragraphs.every(
      (s) => Array.from(s).length >= 5 && Array.from(s).length <= 70
    )
  const generate = async () => {
    setBusy(true)
    setError('')
    try {
      const { data } = await api.post<{ scripts: string[] }>(
        `/live/${avatarId}/host/plan`,
        { topic, material: source },
        { timeout: 105000 }
      )
      setScript(data.scripts.join('\n\n'))
      toast.success('讲稿已生成，请预览后开始主持')
    } catch (error) {
      setError(message(error))
    } finally {
      setBusy(false)
    }
  }
  const control = async (action: string) => {
    setBusy(true)
    setError('')
    try {
      await api.put(`/live/${avatarId}/host`, {
        action,
        scripts: paragraphs,
        min_pause: minPause,
        max_pause: maxPause,
      })
      const { data } = await api.get<HostState>(`/live/${avatarId}/host`)
      setState(data)
    } catch (error) {
      setError(message(error))
    } finally {
      setBusy(false)
    }
  }
  const active =
    state?.config.mode === 'running' && state.status?.phase !== 'finished'
  return (
    <Card>
      <CardHeader>
        <CardTitle>自主主持</CardTitle>
        <CardDescription>
          话术库准备好后持续循环；也可以在下方自定义一轮讲稿。手动口播在当前短段结束后优先播放。
        </CardDescription>
      </CardHeader>
      <CardContent className='flex flex-col gap-4'>
        <section
          aria-label='欢迎话术库'
          className='flex flex-col gap-3 rounded-md border p-4'
        >
          <h3 className='font-medium'>欢迎与陪伴 · 预生成话术库</h3>
          <p className='text-sm text-muted-foreground'>
            {state?.library?.length || 24}{' '}
            条话术，欢迎优先，穿插自我介绍、日常闲聊与轻松互动。
            首次全部生成后开始循环，段间停顿 2～4
            秒；播放期间直接复用视频，手动口播优先。
          </p>
          <div>
            <Button
              disabled={busy || !started || active}
              onClick={() => void control('start_library')}
            >
              准备话术库并循环主持
            </Button>
          </div>
          {!started && (
            <p className='text-xs text-muted-foreground'>
              请先打开数字人直播，再准备话术库。
            </p>
          )}
          <details>
            <summary className='cursor-pointer text-sm'>预览全部话术</summary>
            <ol className='mt-2 list-decimal pl-5 text-sm'>
              {state?.library?.map((text, index) => (
                <li className='mb-2' key={index}>
                  {text}
                </li>
              ))}
            </ol>
          </details>
          <p className='text-xs text-muted-foreground'>
            首次准备需要等待，可查看下方进度。后续同素材、音色和模型配置复用缓存；暂停、继续和结束使用下方按钮。
          </p>
        </section>
        <form
          className='flex flex-col gap-3'
          onSubmit={(event) => {
            event.preventDefault()
            void generate()
          }}
        >
          <Label htmlFor='host-topic'>主持话题</Label>
          <Input
            id='host-topic'
            maxLength={300}
            value={topic}
            onChange={(event) => setTopic(event.target.value)}
            required
          />
          <Label htmlFor='host-source'>参考资料（可从上方项目速读导入）</Label>
          <Textarea
            id='host-source'
            rows={3}
            maxLength={8000}
            value={source}
            onChange={(event) => setSource(event.target.value)}
            placeholder='填写已确认的项目资料，让讲解更具体。'
          />
          <div>
            <Button
              type='submit'
              variant='outline'
              disabled={busy || !topic.trim()}
            >
              {busy ? '处理中…' : '生成主持讲稿'}
            </Button>
          </div>
        </form>
        <Label htmlFor='host-script'>
          分段讲稿（可以修改，每段之间空一行）
        </Label>
        <Textarea
          id='host-script'
          rows={8}
          value={script}
          onChange={(event) => setScript(event.target.value)}
          placeholder={'第一段要说的话。\n\n第二段要说的话。'}
        />
        <p className='text-xs text-muted-foreground'>
          共 {paragraphs.length}/12 段，每段 5～70
          字。提前准备两段素材，讲完本轮后停止；修改草稿不会改变正在主持的任务。
        </p>
        <div className='flex flex-wrap items-center gap-3'>
          <Label htmlFor='host-min'>段间最短停顿（秒）</Label>
          <Input
            className='w-24'
            id='host-min'
            type='number'
            min={10}
            max={60}
            value={minPause}
            onChange={(event) => setMinPause(Number(event.target.value))}
          />
          <Label htmlFor='host-max'>最长停顿（秒）</Label>
          <Input
            className='w-24'
            id='host-max'
            type='number'
            min={10}
            max={60}
            value={maxPause}
            onChange={(event) => setMaxPause(Number(event.target.value))}
          />
        </div>
        <div className='flex flex-wrap items-center gap-2'>
          <Button
            disabled={busy || !started || !valid || active}
            onClick={() => void control('start')}
          >
            开始新一轮主持
          </Button>
          <Button
            variant='outline'
            disabled={busy || !active}
            onClick={() => void control('pause')}
          >
            暂停
          </Button>
          <Button
            variant='outline'
            disabled={busy || !started || !state?.config.run_id || active}
            onClick={() => void control('resume')}
          >
            继续
          </Button>
          <Button
            variant='outline'
            disabled={
              busy || !state?.config.run_id || state.config.mode === 'stopped'
            }
            onClick={() => void control('stop')}
          >
            结束
          </Button>
          <Badge variant='secondary'>
            {state?.config.mode === 'paused'
              ? '已暂停'
              : state?.config.mode === 'stopped'
                ? '已结束'
                : phases[state?.status?.phase || ''] ||
                  (active ? '等待准备' : '尚未开始')}
          </Badge>
        </div>
        {state?.status && (
          <p role='status' className='text-sm text-muted-foreground'>
            {state.config.kind === 'library'
              ? `已循环播放 ${state.status.completed} 条 · 话术库`
              : `已讲 ${state.status.completed}/${state.status.total} 段 · 已准备`}{' '}
            {state.status.prepared}
            {state.config.kind === 'library'
              ? `/${state.status.total} 条已准备`
              : ' 段'}
            {!started ? ' · 请先打开直播' : ''}
          </p>
        )}
        {state?.config.scripts?.length ? (
          <details>
            <summary className='cursor-pointer text-sm'>
              查看当前任务讲稿
            </summary>
            <ol className='mt-2 list-decimal pl-5 text-sm'>
              {state.config.scripts.map((text, index) => (
                <li className='mb-2' key={index}>
                  {text}
                </li>
              ))}
            </ol>
          </details>
        ) : null}
        <p className='text-xs text-muted-foreground'>
          暂停或结束会让当前短段讲完。已有模型请求需要完成后才能处理下一次生成；手动口播会优先于后续主持段落。
        </p>
        {(error || state?.status?.error) && (
          <Alert variant='destructive'>
            <AlertDescription>{error || state?.status?.error}</AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  )
}
