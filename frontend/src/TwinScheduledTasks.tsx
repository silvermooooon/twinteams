import { useEffect, useId, useRef, useState } from 'react';
import type { ApiClient } from './api/client';
import { requestKey } from './api/client';
import { twinApi } from './api/twin';
import type { ScheduledTask, ScheduledTaskInput, ScheduledTaskRun, TaskSchedule } from './api/twin';
import { Empty, Icon, IconButton, Modal } from './ui';

const weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const statusLabels: Record<ScheduledTaskRun['status'] | 'silent', string> = { queued: 'Queued', running: 'Running', completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled', silent: 'Completed quietly' };
const failureText = (failure: unknown) => failure instanceof Error ? failure.message : 'Could not update this task.';
function Notice({ error, retry }: { error: string; retry?: () => void }) {
  return <div className="twin-notice error" role="alert"><Icon name="info" size={18} /><span>{error}</span>{retry && <button type="button" className="text-button" onClick={retry}>Try again</button>}</div>;
}
function timestamp(value: string | null, timezone: string) {
  if (!value) return '—';
  try { return new Intl.DateTimeFormat('en-US', { timeZone: timezone, dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)); }
  catch { return value; }
}
// Only display conversion happens in the browser. Local form values and their IANA zone
// go to the server together, where DST folds/gaps use the same policy as recurrence.
function localDateTime(value: string, timezone: string) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(value));
  const part = (name: string) => parts.find(item => item.type === name)?.value;
  return `${part('year')}-${part('month')}-${part('day')}T${part('hour')}:${part('minute')}`;
}
function intervalLabel(seconds: number) {
  for (const [unit, amount] of [['day', 86400], ['hour', 3600], ['minute', 60]] as const) {
    if (seconds % amount === 0) return `${seconds / amount} ${unit}${seconds === amount ? '' : 's'}`;
  }
  return `${seconds} seconds`;
}
function scheduleLabel(task: ScheduledTask) {
  const rule = task.schedule;
  if (rule.kind === 'once') return `Once · ${timestamp(rule.at, task.timezone)}`;
  if (rule.kind === 'daily') return `Daily at ${rule.time}`;
  if (rule.kind === 'weekly') return `${rule.weekdays.map(day => weekdays[day].slice(0, 3)).join(', ')} at ${rule.time}`;
  return `Every ${intervalLabel(rule.seconds)}`;
}
function intervalFields(rule?: TaskSchedule): [string, number] {
  if (rule?.kind !== 'interval') return ['', 60];
  const unit = [86400, 3600, 60].find(amount => rule.seconds % amount === 0) || 1;
  return [String(rule.seconds / unit), unit];
}

function TaskEditor({ api, task, onSaved, onClose }: { api: ApiClient; task: ScheduledTask | null; onSaved: (task: ScheduledTask) => void; onClose: () => void }) {
  const system = task?.kind === 'system_review';
  const fieldsId = useId();
  const [title, setTitle] = useState(task?.title || '');
  const [instructions, setInstructions] = useState(task?.instructions || '');
  const [timezone, setTimezone] = useState(task?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone);
  const [kind, setKind] = useState<TaskSchedule['kind']>(task?.schedule.kind || 'once');
  const [at, setAt] = useState(task?.schedule.kind === 'once' ? localDateTime(task.schedule.at, task.timezone) : '');
  const [time, setTime] = useState(task?.schedule.kind === 'daily' || task?.schedule.kind === 'weekly' ? task.schedule.time : '');
  const [days, setDays] = useState<number[]>(task?.schedule.kind === 'weekly' ? task.schedule.weekdays : []);
  const [every, setEvery] = useState(intervalFields(task?.schedule)[0]);
  const [unit, setUnit] = useState(intervalFields(task?.schedule)[1]);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const zones = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : [];
  async function save(event: React.FormEvent) {
    event.preventDefault(); if (busy) return;
    setError('');
    try {
      new Intl.DateTimeFormat('en-US', { timeZone: timezone.trim() });
      if (kind === 'weekly' && !days.length) throw new Error('Choose at least one weekday.');
      const seconds = Number(every) * unit;
      if (kind === 'interval' && (!Number.isInteger(seconds) || seconds < 60)) throw new Error('Use an interval of at least 60 seconds.');
      const schedule: TaskSchedule = kind === 'once' ? { kind, at } : kind === 'daily' ? { kind, time } : kind === 'weekly' ? { kind, time, weekdays: [...days].sort((a, b) => a - b) } : { kind, seconds };
      const input: ScheduledTaskInput = { title: title.trim(), instructions: instructions.trim(), timezone: timezone.trim(), schedule };
      setBusy(true);
      const saved = task ? await twinApi(api).updateScheduledTask(task.id, system ? { schedule } : input) : await twinApi(api).createScheduledTask(input);
      onSaved(saved); onClose();
    } catch (failure) { setError(failureText(failure)); } finally { setBusy(false); }
  }
  return <Modal wide title={task ? 'Edit scheduled task' : 'New scheduled task'} onClose={onClose} closeDisabled={busy}>
    <form className="twin-content-editor twin-schedule-editor" onSubmit={save}>
      {system && <p className="twin-help">This system task is managed by TwinTeams. You can change its interval or pause future runs.</p>}
      <label><span id={`${fieldsId}-title`}>Task name</span><input aria-labelledby={`${fieldsId}-title`} autoComplete="off" required maxLength={256} value={title} readOnly={system} disabled={busy} onChange={event => setTitle(event.target.value)} /></label>
      <label><span id={`${fieldsId}-instructions`}>Instructions</span><textarea aria-labelledby={`${fieldsId}-instructions`} required maxLength={20000} value={instructions} readOnly={system} disabled={busy} onChange={event => setInstructions(event.target.value)} /></label>
      <div className="twin-schedule-fields">
        <label><span id={`${fieldsId}-kind`}>Schedule</span><select aria-labelledby={`${fieldsId}-kind`} value={kind} disabled={busy || system} onChange={event => setKind(event.target.value as TaskSchedule['kind'])}><option value="once">Once</option><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="interval">Interval</option></select></label>
        <label><span id={`${fieldsId}-timezone`}>Time zone</span><input aria-labelledby={`${fieldsId}-timezone`} required list="twin-schedule-timezones" value={timezone} readOnly={system} disabled={busy} onChange={event => setTimezone(event.target.value)} /><datalist id="twin-schedule-timezones"><option value="UTC" />{zones.map(zone => <option key={zone} value={zone} />)}</datalist></label>
      </div>
      {kind === 'once' && <label><span id={`${fieldsId}-at`}>Date and time</span><input aria-labelledby={`${fieldsId}-at`} required type="datetime-local" value={at} disabled={busy} onChange={event => setAt(event.target.value)} /></label>}
      {(kind === 'daily' || kind === 'weekly') && <label><span id={`${fieldsId}-time`}>Time</span><input aria-labelledby={`${fieldsId}-time`} required type="time" value={time} disabled={busy} onChange={event => setTime(event.target.value)} /></label>}
      {kind === 'weekly' && <fieldset className="twin-schedule-weekdays"><legend>Weekdays</legend>{weekdays.map((day, index) => <button key={day} type="button" aria-pressed={days.includes(index)} disabled={busy} onClick={() => setDays(previous => previous.includes(index) ? previous.filter(value => value !== index) : [...previous, index])}>{day.slice(0, 3)}</button>)}</fieldset>}
      {kind === 'interval' && <div className="twin-schedule-fields"><label><span id={`${fieldsId}-every`}>Repeat every</span><input aria-labelledby={`${fieldsId}-every`} type="number" required min={1} step={1} value={every} disabled={busy} onChange={event => setEvery(event.target.value)} /></label><label><span id={`${fieldsId}-unit`}>Interval unit</span><select aria-labelledby={`${fieldsId}-unit`} value={unit} disabled={busy} onChange={event => setUnit(Number(event.target.value))}><option value={1}>Seconds</option><option value={60}>Minutes</option><option value={3600}>Hours</option><option value={86400}>Days</option></select></label></div>}
      <p className="twin-help">{kind === 'interval' ? 'At least 60 seconds between runs.' : 'Dates and times use the selected time zone. If a daylight saving change skips a time, it moves forward; a repeated time runs once.'} Tasks run in your private Twin conversation. Successful reviews can finish quietly.</p>
      {error && <Notice error={error} />}
      <div className="twin-content-actions"><button type="button" className="secondary-button" disabled={busy} onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy}>{busy ? 'Saving…' : 'Save task'}</button></div>
    </form>
  </Modal>;
}

function TaskHistory({ api, task, revision, onClose }: { api: ApiClient; task: ScheduledTask; revision: number; onClose: () => void }) {
  const [items, setItems] = useState<ScheduledTaskRun[] | null>(null); const [error, setError] = useState(''); const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setError('');
    twinApi(api).scheduledTaskHistory(task.id, controller.signal).then(result => { if (!controller.signal.aborted) setItems(result.items); }).catch(failure => { if (!controller.signal.aborted) setError(failureText(failure)); });
    return () => controller.abort();
  }, [api, task.id, revision, retry]);
  return <Modal wide title={`Run history · ${task.title}`} onClose={onClose}>
    <p className="twin-help">The latest 20 runs. Times shown in {task.timezone}. A completed review may produce no chat message.</p>
    {error && <Notice error={error} retry={() => setRetry(value => value + 1)} />}
    {items === null && !error ? <div className="twin-loading" role="status"><span className="spinner" />Loading run history…</div> : !items?.length && !error ? <Empty icon="clock" title="No runs yet" /> : <ol className="twin-schedule-history">{items?.map(run => <li key={run.request_id} data-request-id={run.request_id}><div><strong className={`twin-schedule-status ${run.status}`}>{statusLabels[run.status]}</strong><time dateTime={run.accepted_at}>{timestamp(run.accepted_at, task.timezone)}</time></div><dl><div><dt>Trigger</dt><dd>{run.trigger === 'manual' ? 'Run now' : run.planned_at ? `Scheduled for ${timestamp(run.planned_at, task.timezone)}` : 'Scheduled'}</dd></div><div><dt>Request</dt><dd><code>{run.request_id}</code></dd></div>{run.run_id && <div><dt>Run</dt><dd><code>{run.run_id}</code></dd></div>}</dl>{run.error && <p className="twin-inline-error" role="alert">{run.error}</p>}</li>)}</ol>}
  </Modal>;
}

export function ScheduledTasksTab({ api, revision }: { api: ApiClient; revision: number }) {
  const [items, setItems] = useState<ScheduledTask[] | null>(null); const [loadError, setLoadError] = useState(''); const [retry, setRetry] = useState(0);
  const [editor, setEditor] = useState<{ task: ScheduledTask | null } | null>(null); const [historyId, setHistoryId] = useState<string | null>(null); const [deleting, setDeleting] = useState<ScheduledTask | null>(null);
  const [busy, setBusy] = useState<string[]>([]); const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [historyRevision, setHistoryRevision] = useState(0);
  const runKeys = useRef(new Map<string, string>());
  useEffect(() => {
    const controller = new AbortController(); setLoadError('');
    twinApi(api).scheduledTasks(controller.signal).then(result => { if (!controller.signal.aborted) setItems(result.items); }).catch(failure => { if (!controller.signal.aborted) setLoadError(failureText(failure)); });
    return () => controller.abort();
  }, [api, revision, retry]);
  const replace = (task: ScheduledTask) => setItems(previous => previous?.some(item => item.id === task.id) ? previous.map(item => item.id === task.id ? task : item) : [...(previous || []), task]);
  async function action(id: string, operation: () => Promise<void>) {
    if (busy.includes(id)) return;
    setBusy(previous => [...previous, id]); setError(''); setNotice('');
    try { await operation(); } catch (failure) { setError(failureText(failure)); } finally { setBusy(previous => previous.filter(value => value !== id)); }
  }
  const historyTask = items?.find(task => task.id === historyId);
  return <>
    <div className="twin-section-heading"><div><h2>Scheduled tasks</h2><p>Tell your Twin what you need and when in your private chat. Review its tasks here.</p></div><div className="twin-row-actions"><IconButton icon="plus" label="New task" onClick={() => setEditor({ task: null })} /><IconButton icon="refresh" label="Refresh scheduled tasks" onClick={() => { setRetry(value => value + 1); setHistoryRevision(value => value + 1); }} /></div></div>
    <p className="twin-help">Pausing prevents future runs. Stopping cancels work that is already queued or running.</p>
    {loadError && <Notice error={loadError} retry={() => setRetry(value => value + 1)} />}{error && <Notice error={error} />}{notice && <p className="twin-save-status" role="status">{notice}</p>}
    {items === null && !loadError ? <div className="twin-loading" role="status"><span className="spinner" />Loading scheduled tasks…</div> : !items?.length && !loadError ? <Empty icon="clock" title="No scheduled tasks" detail="Tell your Twin what you need and when in your private chat." /> : <div className="twin-schedule-list">{items?.map(task => <article className="twin-schedule-card" key={task.id} data-task-id={task.id} data-task-kind={task.kind}>
      <header><span className="twin-list-icon"><Icon name="clock" size={21} /></span><div className="twin-list-copy"><h3>{task.title}</h3><p>{scheduleLabel(task)} · {task.timezone}</p></div><span className={`twin-schedule-badge ${task.enabled ? '' : 'paused'}`}>{task.enabled ? 'Enabled' : 'Paused'}</span></header>
      {task.kind === 'system_review' && <p className="twin-help twin-schedule-managed">System managed · Reviews ongoing work and pending items. Instructions are read-only.</p>}
      <p className="twin-schedule-instructions">{task.instructions}</p>
      <dl className="twin-schedule-summary"><div><dt>Next run</dt><dd>{task.enabled && task.next_run_at ? timestamp(task.next_run_at, task.timezone) : task.enabled ? 'No future run' : 'Paused'}</dd></div><div><dt>Last result</dt><dd>{task.pending_work && !task.active_request_id ? <span className="twin-schedule-status queued">Waiting for pending work</span> : task.last_status ? <><span className={`twin-schedule-status ${task.last_status}`}>{statusLabels[task.last_status]}</span>{task.last_run_at && <> · {timestamp(task.last_run_at, task.timezone)}</>}</> : 'No runs yet'}</dd></div></dl>
      {task.last_error && <p className="twin-inline-error" role="alert">{task.last_error}</p>}
      <div className="twin-row-actions twin-schedule-actions">
        <IconButton icon={task.enabled ? 'pause' : 'refresh'} label={`${task.enabled ? 'Pause future runs for' : 'Resume future runs for'} ${task.title}`} disabled={busy.includes(task.id)} onClick={() => { void action(task.id, async () => { replace(await twinApi(api).updateScheduledTask(task.id, { enabled: !task.enabled })); }); }} />
        <IconButton icon="play" label={`Run now ${task.title}`} disabled={busy.includes(task.id) || task.pending_work || !!task.active_request_id} onClick={() => { void action(task.id, async () => { const key = runKeys.current.get(task.id) || requestKey(); runKeys.current.set(task.id, key); const result = await twinApi(api).runScheduledTask(task.id, key); runKeys.current.delete(task.id); replace(result.task); setNotice(`Run queued for ${task.title}.`); setHistoryRevision(value => value + 1); }); }} />
        {task.active_request_id && <IconButton icon="stop" className="twin-schedule-stop" label={`Stop active run for ${task.title}`} disabled={busy.includes(task.id)} onClick={() => { void action(task.id, async () => { await api.post(`/reply-requests/${encodeURIComponent(task.active_request_id!)}/cancel`); setNotice(`Stop requested for ${task.title}. Future scheduled runs are ${task.enabled ? 'enabled' : 'paused'}.`); setRetry(value => value + 1); setHistoryRevision(value => value + 1); }); }} />}
        <IconButton icon="edit" label={`Edit ${task.title}`} disabled={busy.includes(task.id)} onClick={() => setEditor({ task })} />
        <IconButton icon="clock" label={`Run history for ${task.title}`} onClick={() => setHistoryId(task.id)} />
        {task.kind === 'user' && <IconButton icon="trash" className="twin-schedule-delete" label={`Delete ${task.title}`} disabled={busy.includes(task.id) || task.pending_work || !!task.active_request_id} onClick={() => setDeleting(task)} />}
      </div>
    </article>)}</div>}
    {editor && <TaskEditor api={api} task={editor.task} onClose={() => setEditor(null)} onSaved={replace} />}
    {historyTask && <TaskHistory api={api} task={historyTask} revision={revision + historyRevision} onClose={() => setHistoryId(null)} />}
    {deleting && <Modal title="Delete scheduled task?" onClose={() => setDeleting(null)} closeDisabled={busy.includes(deleting.id)}><p className="twin-help">Delete {deleting.title}? Future runs will be removed. Stop any active work before deleting.</p>{error && <Notice error={error} />}<div className="twin-content-actions"><button type="button" className="secondary-button" disabled={busy.includes(deleting.id)} onClick={() => setDeleting(null)}>Cancel</button><button type="button" className="danger-button" disabled={busy.includes(deleting.id)} onClick={() => { void action(deleting.id, async () => { await twinApi(api).deleteScheduledTask(deleting.id); setItems(previous => previous?.filter(task => task.id !== deleting.id) || []); setDeleting(null); }); }}>Delete task</button></div></Modal>}
  </>;
}
