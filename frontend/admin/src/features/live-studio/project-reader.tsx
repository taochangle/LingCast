import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'

type ProjectResult = {
  repository: string
  commit: string
  script: string
  sources: { path: string; url: string; truncated: boolean }[]
  warnings: string[] | null
}

function errorMessage(error: unknown) {
  return (error as { response?: { data?: { error?: string } } }).response?.data?.error || '请求失败，请稍后重试'
}

export function ProjectReader({ avatarId, started, onMaterial }: { avatarId: number; started: boolean; onMaterial?: (topic: string, text: string) => void }) {
  const [url, setUrl] = useState('')
  const [focus, setFocus] = useState('介绍项目用途、技术栈和核心实现')
  const [result, setResult] = useState<ProjectResult | null>(null)
  const [script, setScript] = useState('')
  const [reading, setReading] = useState(false)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const controller = useRef<AbortController | null>(null)
  useEffect(() => () => controller.current?.abort(), [])

  const read = async () => {
    if (reading) return
    setReading(true)
    setError('')
    setResult(null)
    setScript('')
    setSubmitted(false)
    const abort = new AbortController()
    controller.current = abort
    try {
      const { data } = await api.post<ProjectResult>('/projects/read', { url, focus }, { timeout: 115000, signal: abort.signal })
      setResult(data)
      setScript(data.script)
    } catch (error) {
      if (!abort.signal.aborted) setError(errorMessage(error))
    } finally { setReading(false) }
  }
  const speak = async () => {
    if (!started || sending || !script.trim()) return
    setSending(true)
    setError('')
    try {
      await api.post(`/live/${avatarId}/narrate`, { text: script.trim() })
      setSubmitted(true)
      toast.success('讲解已提交，生成完成后自动播放')
    } catch (error) { setError(errorMessage(error)) }
    finally { setSending(false) }
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>GitHub 项目速读</CardTitle>
        <CardDescription>读取公开仓库的部分说明和主要代码，生成一段简短讲解。先预览改稿，再让数字人按原文说。</CardDescription>
      </CardHeader>
      <CardContent className='flex flex-col gap-3'>
        <form className='flex flex-col gap-3' onSubmit={(event) => { event.preventDefault(); void read() }}>
          <Label htmlFor='project-url'>GitHub 仓库链接</Label>
          <Input id='project-url' type='url' placeholder='https://github.com/作者/仓库' value={url} onChange={(event) => setUrl(event.target.value)} disabled={reading} required />
          <Label htmlFor='project-focus'>讲解重点</Label>
          <Input id='project-focus' maxLength={500} value={focus} onChange={(event) => setFocus(event.target.value)} disabled={reading} />
          <div><Button type='submit' disabled={reading || !url.trim()}>{reading ? '正在读取文件并生成讲解…' : '读取项目，生成讲解稿'}</Button></div>
        </form>
        {reading && <p role='status' className='text-sm text-muted-foreground'>正在读取默认分支的固定版本，通常需要几十秒；此时不会自动口播。</p>}
        {error && <p role='alert' className='text-sm text-destructive'>{error}</p>}
        {result && <>
          <p className='text-sm text-muted-foreground'>{result.repository} · 版本 {result.commit.slice(0, 8)} · 已读取 {result.sources.length} 个文件（部分文件可能节选）</p>
          <Label htmlFor='project-script'>讲解稿（可以修改）</Label>
          <Textarea id='project-script' rows={6} value={script} onChange={(event) => { setScript(event.target.value); setSubmitted(false) }} />
          <p className='text-xs text-muted-foreground'>{Array.from(script.trim()).length}/220 字。声音与口型边生成边播；首段就绪即播，未就绪时回到待机。</p>
          <div><Button onClick={() => void speak()} disabled={!started || sending || submitted || !script.trim() || Array.from(script.trim()).length > 220}>{sending ? '提交中…' : submitted ? '已提交讲解' : '按原文讲解'}</Button></div>
          {!started && <p className='text-sm text-muted-foreground'>请先点击右上角「打开直播」，再提交讲解。</p>}
          <div><Button variant='outline' onClick={() => { onMaterial?.(`介绍 ${result.repository}`, `仓库：${result.repository}\n提交版本：${result.commit}\n速读结论（仅部分文件）：${script}`); toast.success('已导入下方自主主持参考资料') }}>用作自主主持资料</Button></div>
          <details>
            <summary className='cursor-pointer text-sm'>查看读取依据</summary>
            <ul className='mt-2 flex flex-col gap-1 text-sm'>{result.sources.map((source) => <li key={source.path}><a className='underline' href={source.url} target='_blank' rel='noreferrer'>{source.path}</a>{source.truncated ? '（节选）' : ''}</li>)}</ul>
            {result.warnings?.map((warning) => <p key={warning} className='text-sm text-muted-foreground'>{warning}</p>)}
          </details>
        </>}
      </CardContent>
    </Card>
  )
}
