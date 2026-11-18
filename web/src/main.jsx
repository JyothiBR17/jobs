import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { PublicClientApplication } from '@azure/msal-browser';
import './style.css';

const pages = ['Overview', 'Job Discovery', 'Recommended Jobs', 'Sponsorship Verification',
  'Agentic Autopilot', 'Application Approval Centre', 'Submitted Applications', 'CV Library', 'Cover Letter Library',
  'Interview Tracker', 'Analytics', 'Agent Activity', 'Integrations', 'Settings', 'Security and Audit Logs'];
const statuses = {confirmed:'Confirmed', conditional:'Conditional', licensed_only:'Licence only',
  unavailable:'Unavailable', unknown:'Unknown'};
const format = value => value ? new Date(value).toLocaleString() : '—';
const exportFile = (name, text) => {
  const url = URL.createObjectURL(new Blob([text], {type:'text/plain'}));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

function App() {
  const [config, setConfig] = useState();
  const [token, setToken] = useState('');
  const [entered, setEntered] = useState('');
  const [page, setPage] = useState('Overview');
  const [jobs, setJobs] = useState([]);
  const [drafts, setDrafts] = useState([]);
  const [tracking, setTracking] = useState({submissions:[],events:[]});
  const [profile, setProfile] = useState('');
  const [stored, setStored] = useState(false);
  const [events, setEvents] = useState([]);
  const [usage, setUsage] = useState({});
  const [discovery, setDiscovery] = useState({status:'idle'});
  const [autopilot, setAutopilot] = useState(null);
  const [search, setSearch] = useState('');
  const [country, setCountry] = useState('');
  const [filter, setFilter] = useState('');
  const [selection, setSelection] = useState([]);
  const [dialog, setDialog] = useState(null);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [client, setClient] = useState();
  const [ready, setReady] = useState(false);

  async function api(path, method='GET', data) {
    const active = client?.getActiveAccount();
    const credential = active ? (await client.acquireTokenSilent({account:active, scopes:[config.scope]})).accessToken : token;
    const headers = {Authorization:'Bearer ' + credential};
    if (data && !(data instanceof FormData)) headers['Content-Type'] = 'application/json';
    const response = await fetch('/api' + path, {method, headers, body:data instanceof FormData ? data : data ? JSON.stringify(data) : undefined});
    const body = await response.json();
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Check the submitted fields.');
    return body;
  }

  async function reload() {
    const [j,d,t,a,p,u,s,auto] = await Promise.all(['/jobs','/drafts','/tracking','/audit','/profile','/usage','/discovery/status','/autopilot'].map(path => api(path)));
    setJobs(j); setDrafts(d); setTracking(t); setEvents(a); setUsage(u); setDiscovery(s);
    setProfile(JSON.stringify(p.profile || {name:'',summary:'',notice_days:90,facts:[],source_verified:false},null,2));
    setStored(p.resume_stored); setAutopilot(auto); setSelection([]); setReady(true);
  }

  useEffect(() => {
    fetch('/api/config').then(r => r.json()).then(async c => {
      setConfig(c);
      if (c.production) {
        const instance = new PublicClientApplication({auth:{clientId:c.client,
          authority:'https://login.microsoftonline.com/'+c.tenant, redirectUri:window.location.origin},
          cache:{cacheLocation:'sessionStorage'}, system:{allowPlatformBroker:false}});
        await instance.initialize();
        const response = await instance.handleRedirectPromise();
        if (response?.account || instance.getAllAccounts()[0]) instance.setActiveAccount(response?.account || instance.getAllAccounts()[0]);
        setClient(instance);
      }
    }).catch(e => setError(e.message));
  }, []);
  useEffect(() => {
    if (token || client?.getActiveAccount()) reload().catch(e => {setError(e.message);setReady(false);});
  }, [token,client]);

  useEffect(() => {
    if (!ready || discovery.status !== 'running') return;
    const interval = setInterval(() => {
      api('/discovery/status').then(async next => {
        setDiscovery(next);
        if (next.status !== 'running') {
          const [freshJobs, freshDrafts] = await Promise.all([api('/jobs'), api('/drafts')]);
          setJobs(freshJobs); setDrafts(freshDrafts);
        }
      }).catch(e => setError(e.message));
    }, 5000);
    return () => clearInterval(interval);
  }, [ready, discovery.status, token, client]);

  async function action(fn, message) {
    setError(''); setNotice('');
    try { await fn(); setDialog(null); await reload(); setNotice(message); }
    catch (e) { setError(e.message); }
  }

  if (!ready) return <div className="login"><div className="brand">↗ <span>AI International Job Hunter</span></div>
    <section><h1>Sign in</h1><p>Access your private international job search workspace.</p>
    {error && <p role="alert">{error}</p>}
    {config?.production ? <button disabled={!client} onClick={() => client.loginRedirect({scopes:[config.scope]}).catch(e=>setError(e.message))}>Sign in with Microsoft</button> :
    config && <form onSubmit={e=>{e.preventDefault();setToken(entered.trim());}}><label>Local access token<input type="password" required value={entered} onChange={e=>setEntered(e.target.value)}/></label><button>Sign in</button></form>}</section></div>;

  const eligibleIds = new Set(jobs.filter(j=>j.eligible).map(j=>j.id));
  const queue = drafts.filter(d=>d.state==='review');
  const selected = queue.filter(d=>selection.includes(d.vacancy_id));
  const verification = jobs.filter(j=>j.active && (!['confirmed','conditional','unavailable'].includes(j.status)));
  const visible = (page==='Recommended Jobs' ? jobs.filter(j=>j.eligible) : page==='Sponsorship Verification' ? verification : jobs)
    .filter(j=>(!country || j.country===country) && (!filter || (j.status||'unknown')===filter) &&
      (j.title+' '+j.employer+' '+j.location).toLowerCase().includes(search.toLowerCase()));
  const stats = [
    ['Jobs discovered',jobs.length],['New today',jobs.filter(j=>new Date(j.discovered).toDateString()===new Date().toDateString()).length],
    ['Recommended',eligibleIds.size],['Confirmed sponsorship',jobs.filter(j=>j.status==='confirmed'&&j.active).length],
    ['Sponsorship review',verification.length],['Prepared',drafts.length],['Awaiting approval',queue.length],
    ['Approved',drafts.filter(d=>d.state==='approved').length],['Submissions recorded',tracking.submissions.length],
    ['Recruiter responses',tracking.events.filter(e=>e.status==='recruiter_response').length],
    ['Interviews',tracking.events.filter(e=>e.status==='interview').length],['Rejections',tracking.events.filter(e=>e.status==='rejected').length],
  ];

  function jobTable(items) {
    return <div className="table"><table><thead><tr>{['Role','Location','Salary','Fit','Sponsorship','Discovered',''].map(h=><th key={h}>{h}</th>)}</tr></thead>
      <tbody>{items.map(j=><tr key={j.id}><td><strong>{j.title}</strong><div className="subtle">{j.employer}{j.active ? '' : ' · Closed'}</div></td>
      <td>{j.country}<div className="subtle">{j.location}</div></td><td>{j.detail.salary||'—'}</td>
      <td><span className="score">{j.score ?? '—'}</span></td><td><span className={'pill '+(j.status||'unknown')}>{statuses[j.status||'unknown']}</span></td>
      <td>{format(j.discovered)}</td><td><button className="minor" onClick={()=>setDialog({type:'job',job:j})}>View</button></td></tr>)}</tbody></table>
      {!items.length && <p className="empty">No vacancies match this view.</p>}</div>;
  }

  return <div className="shell"><aside><div className="brand">↗ <span>International<br/>Job Hunter</span></div>
    <nav>{pages.map(p=><button className={p===page?'active':''} key={p} onClick={()=>{setPage(p);setSearch('');setCountry('');setFilter('');setError('');setNotice('');}}>{p}</button>)}</nav>
    <button className="signout" onClick={()=>client ? client.logoutRedirect() : window.location.reload()}>Sign out</button></aside>
    <main><header><div><p className="eyebrow">INTERNATIONAL SEARCH</p><h1>{page}</h1></div><button className="minor" onClick={()=>action(async()=>{},'Updated')}>Refresh</button></header>
    {error && <p className="message error" role="alert">{error}</p>}{notice && <p className="message" role="status">{notice}</p>}
    {['Overview','Analytics'].includes(page) && <><div className="metrics">{stats.map(([label,value])=><article key={label}><span>{label}</span><strong>{value}</strong></article>)}</div>
      {page==='Analytics' && <section><h2>Model usage · {usage.month}</h2><p>{usage.requests} / {usage.request_limit} requests · {usage.input_tokens} input tokens · {usage.output_tokens} output tokens</p></section>}
      {page==='Overview' && <><h2>Recent discoveries</h2>{jobTable(jobs.slice(0,6))}</>}</>}
    {['Job Discovery','Recommended Jobs','Sponsorship Verification'].includes(page) && <>{page==='Job Discovery' &&
      <div className="toolbar">
        <button disabled={discovery.status==='running'} onClick={()=>action(()=>api('/discovery/run','POST'),'Discovery started. Use Refresh to see newly discovered vacancies.')}>Discover jobs now</button>
        <span role="status">Discovery: {discovery.status}{discovery.started ? ' · started '+format(discovery.started) : ''}</span>
        {discovery.error && <span role="alert">Last run: {discovery.error}</span>}
      </div>}<div className="toolbar">
      <input aria-label="Search roles and employers" placeholder="Search roles or employers" value={search} onChange={e=>setSearch(e.target.value)}/>
      <select aria-label="Country" value={country} onChange={e=>setCountry(e.target.value)}><option value="">All countries</option>{[...new Set(jobs.map(j=>j.country))].sort().map(c=><option key={c}>{c}</option>)}</select>
      <select aria-label="Sponsorship" value={filter} onChange={e=>setFilter(e.target.value)}><option value="">All sponsorship statuses</option>{Object.entries(statuses).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select>
      </div>{jobTable(visible)}</>}
    {page==='Agentic Autopilot' && autopilot && <div className="autopilot-page">
      <section className="autopilot-hero">
        <p className="eyebrow">AUTONOMOUS INTERNATIONAL JOB SEARCH</p>
        <h2>AI agent orchestration · Global except India</h2>
        <p>Every 30 minutes, the worker discovers vacancies, scores evidence against your verified CV, checks sponsorship evidence and prepares applications. It submits only through authorized employer APIs and records real confirmation IDs.</p>
        <div className="agent-chips">
          <span className={autopilot.policy.enabled?'indicator online':'indicator'}>{autopilot.policy.enabled?'● Autopilot enabled':'○ Autopilot paused'}</span>
          <span className={autopilot.openai_enabled?'indicator online':'indicator'}>{autopilot.openai_enabled?'● Azure OpenAI connected':'○ AI model unavailable'}</span>
          <span className="indicator">↗ Non-India job locations only</span>
          <span className="indicator">↻ 30-minute continuous cycle</span>
        </div>
        <div className="toolbar"><button disabled={discovery.status==='running'} onClick={()=>action(()=>api('/autopilot/run','POST'),'Agent cycle started')}>Run all agents now</button>
          <span>Last cycle: {autopilot.last_cycle?format(autopilot.last_cycle.time):'Not run yet'}</span></div>
      </section>
      <div className="metrics">
        {[
          ['Confirmed employer submissions',autopilot.counts.confirmed||0],
          ['Requires your attention',autopilot.counts.needs_review||0],
          ['Uncertain outcomes',autopilot.counts.unknown||0],
          ['Rate-limited',autopilot.counts.rate_limited||0],
        ].map(([label,value])=><article key={label}><span>{label}</span><strong>{value}</strong></article>)}
      </div>
      <section>
        <h2>Connect OpenAI intelligence</h2>
        <p>{autopilot.openai_enabled ? (autopilot.ai_source==='aegissoc_shared'?'Connected to the existing AegisSOC Azure OpenAI model (GPT-4.1 mini). The Job Hunter app and credentials remain in UK South; model inference uses AegisSOC’s East US / global deployment. No AegisSOC incident data, SecOps application or knowledge index is accessed.':'OpenAI model connected for evidence-grounded analysis of your verified candidate facts.') : 'No working AI deployment is configured. Enter your own OpenAI API key only if you prefer a separate provider. It will be secured in the Job Hunter UK South Key Vault.'}</p>
        {autopilot.ai_source==='aegissoc_shared'&&<p className="subtle">Source: AegisSOC Azure OpenAI · Processing: East US / Global Standard · Shared model usage may incur charges on the existing Azure subscription.</p>}
        {!autopilot.openai_enabled&&<form onSubmit={e=>{e.preventDefault();const key=e.currentTarget.elements.openaiKey.value;
          action(()=>api('/agent/credentials/openai','POST',{key}),'OpenAI key securely stored in UK South');e.currentTarget.reset();}}>
          <label>OpenAI project API key<input type="password" name="openaiKey" autoComplete="off" placeholder="sk-proj-…" minLength={20} required/></label>
          <button>Connect OpenAI to my agents</button>
        </form>}
      </section>
      <section>
        <h2>Worldwide public job discovery</h2>
        <p><strong>{autopilot.discovery_board_count||0} public employer boards configured.</strong> The worker checks published listings on Greenhouse, Lever and Ashby, uses your verified CV to rank relevant cloud and security roles, and excludes jobs located in India. These public feeds are not employer permission to submit applications.</p>
        <details>
          <summary>View configured public boards (discovery only)</summary>
          <textarea className="public-sources" readOnly rows={7}
            value={(autopilot.discovery_sources||[]).map(item=>item.source).join(',')} 
            aria-label="Public discovery board identifiers" />
          <p className="subtle">Copying these identifiers into an auto-apply field does not grant employer API authorization. New employers can be added to the candidate directory and checked before activation.</p>
        </details>
      </section>
      <section>
        <h2>Autonomous application settings</h2>
        <p>Only enter boards for which an employer has actually issued application-submission API credentials to your integration. Public board access is not sufficient. Automatic applications additionally require your verified résumé, contact details, explicit consent, verified eligibility and a real employer confirmation.</p>
        <form onSubmit={e=>{e.preventDefault();const data=new FormData(e.currentTarget);
          const policy={enabled:data.get('enabled')==='on',consent:data.get('consent')==='on',
            email:String(data.get('email')||'').trim(),phone:String(data.get('phone')||'').trim(),
            min_score:Number(data.get('min_score')),max_daily:Number(data.get('max_daily')),
            sources:String(data.get('sources')||'').split(',').map(s=>s.trim()).filter(Boolean)};
          action(()=>api('/autopilot','PUT',policy),'Autopilot policy saved');}}>
          <div className="auto-grid">
            <label>Application contact email<input type="email" name="email" defaultValue={autopilot.policy.email} required/></label>
            <label>Phone (optional)<input type="tel" name="phone" defaultValue={autopilot.policy.phone}/></label>
            <label>Minimum AI match score<input type="number" name="min_score" min="70" max="100" defaultValue={autopilot.policy.min_score} required/></label>
            <label>Daily application cap<input type="number" name="max_daily" min="1" max="20" defaultValue={autopilot.policy.max_daily} required/></label>
          </div>
          <label>Authorized automatic-submission boards (comma-separated; actual employer-issued access required)
            <input name="sources" defaultValue={autopilot.policy.sources.join(',')} placeholder="lever:employer-with-api-key, lever-eu:employer-with-api-key"/>
          </label>
          <label className="inline-check"><input type="checkbox" name="consent" defaultChecked={autopilot.policy.consent}/> I authorize the agent to send my verified CV, contact details and cover letter to employers on these approved boards.</label>
          <label className="inline-check"><input type="checkbox" name="enabled" defaultChecked={autopilot.policy.enabled}/> Enable automatic submission when all eligibility and provider checks pass.</label>
          <button>Save and apply autopilot policy</button>
        </form>
        <p className="subtle">Readiness: {autopilot.original_resume_uploaded?'PDF uploaded':'Upload original PDF'} · {autopilot.profile_verified?'Profile verified':'Verify profile'} · {autopilot.openai_enabled?'AI active: '+(autopilot.model||'connected'):'AI model unavailable'} · {autopilot.provider_api_ready.length} boards with verified API configuration</p>
      </section>
      <section>
        <h2>Application execution ledger</h2>
        {autopilot.attempts.length===0?<p className="empty">No external application attempts. Submitted counts remain zero until an employer confirms receipt.</p>:<div className="table"><table>
          <thead><tr><th>Job</th><th>Country</th><th>State</th><th>Evidence</th><th>Last update</th></tr></thead>
          <tbody>{autopilot.attempts.map(a=><tr key={a.vacancy_id}>
            <td><strong>{a.title}</strong><div className="subtle">{a.employer}</div></td><td>{a.country}</td>
            <td><span className={'pill '+(a.state==='confirmed'?'confirmed':'unknown')}>{a.state.replaceAll('_',' ')}</span></td>
            <td>{a.state==='confirmed'?'Employer ID: '+a.reference:a.detail}</td><td>{format(a.updated_at)}</td></tr>)}</tbody>
        </table></div>}
      </section>
    </div>}
    {page==='Application Approval Centre' && <><div className="toolbar">
      <button className="minor" onClick={()=>setSelection(queue.filter(d=>eligibleIds.has(d.vacancy_id)).map(d=>d.vacancy_id))}>Select eligible</button>
      <button disabled={!selected.length} onClick={()=>setDialog({type:'batch',decision:'approved'})}>Approve selected</button>
      <button className="minor" disabled={!selected.length} onClick={()=>setDialog({type:'batch',decision:'rejected'})}>Reject selected</button>
      <button className="minor" disabled={!selected.length} onClick={()=>exportFile('applications.json',JSON.stringify(selected,null,2))}>Export selected</button></div>
      {drafts.length===0 && <p className="empty">Applications appear here after scoring and sponsorship review.</p>}
      {drafts.map(d=><article className="list" key={d.vacancy_id}><div>{d.state==='review'&&<input type="checkbox" aria-label={'Select '+d.title+' at '+d.employer} checked={selection.includes(d.vacancy_id)} onChange={e=>setSelection(e.target.checked?[...selection,d.vacancy_id]:selection.filter(id=>id!==d.vacancy_id))}/>}
      <strong>{d.title}</strong><p>{d.employer} · {d.country} · {d.score ?? '—'} · {statuses[d.status||'unknown']}</p></div>
      <span className="pill">{d.state}</span><button className="minor" onClick={()=>setDialog({type:'draft',draft:d})}>Preview</button></article>)}</>}
    {['CV Library','Cover Letter Library'].includes(page) && drafts.map(d=><article className="list" key={d.vacancy_id}><strong>{d.title} · {d.employer}</strong>
      <button className="minor" onClick={()=>setDialog({type:'document',text:page==='CV Library'?d.cv:d.letter})}>Preview</button></article>)}
    {['Submitted Applications','Interview Tracker'].includes(page) && <>{tracking.submissions.map(s=><article className="list" key={s.vacancy_id}>
      <div><strong>{s.employer} · {s.title}</strong><p>{s.channel==='lever_api'?'Employer API confirmed':'Candidate-reported'} · {s.reference}</p></div>
      <button className="minor" onClick={()=>setDialog({type:'track',id:s.vacancy_id})}>Add update</button></article>)}
      {tracking.events.filter(e=>page!=='Interview Tracker'||e.status==='interview').map(e=><article className="list" key={e.id}><strong>{e.status.replaceAll('_',' ')}</strong><span>{e.description}</span><time>{format(e.event_time)}</time></article>)}
      {!tracking.submissions.length && <p className="empty">No submissions recorded.</p>}</>}
    {['Security and Audit Logs','Agent Activity'].includes(page) && <div className="table"><table><thead><tr><th>Time</th><th>Actor</th><th>Event</th><th>Record</th></tr></thead><tbody>
      {events.filter(e=>page!=='Agent Activity'||['discovery','matching','sponsorship_registry','autopilot','autopilot-evidence','autopilot-drafter'].includes(e.actor)).map(e=><tr key={e.id}><td>{format(e.time)}</td><td>{e.actor}</td><td>{e.action}</td><td>{e.target}</td></tr>)}</tbody></table></div>}
    {page==='Integrations' && <section><h2>Provider configuration</h2><p>Configure employer board feeds, a project-scoped OpenAI API key, and employer-approved application API credentials as deployment secrets. See Agentic Autopilot for execution history; unsupported forms remain in the review queue.</p></section>}
    {page==='Settings' && <section><h2>Candidate profile</h2><p>Save candidate facts, upload the original PDF, then set source_verified to true after comparing the facts against the résumé.</p>
      <label>Import candidate profile JSON<input type="file" accept=".json,application/json" onChange={async e=>{const file=e.target.files[0]; if (file) setProfile(await file.text());}}/></label>
      <form onSubmit={e=>{e.preventDefault();action(()=>api('/profile','PUT',JSON.parse(profile)),'Profile saved');}}><textarea aria-label="Candidate profile JSON" value={profile} onChange={e=>setProfile(e.target.value)} rows={15}/><button>Save profile</button></form>
      <p>{stored?'Original résumé stored':'Original résumé missing'}</p>
      {!stored&&<form onSubmit={e=>{e.preventDefault();const data=new FormData(e.currentTarget);action(()=>api('/resume','POST',data),'Résumé uploaded');}}><input aria-label="Original résumé PDF" type="file" name="file" accept="application/pdf" required/><button>Upload PDF</button></form>}</section>}
    </main>
    {dialog && <div className="overlay" onClick={()=>setDialog(null)}><section role="dialog" aria-modal="true" aria-label="Review details" onClick={e=>e.stopPropagation()}>
      <button className="minor close" onClick={()=>setDialog(null)}>Close</button>
      {error&&<p className="message error" role="alert">{error}</p>}
      {dialog.type==='job' && (()=>{const j=dialog.job;return <><h2>{j.title}</h2><p>{j.employer} · {j.location}</p><a href={j.url} target="_blank" rel="noreferrer">Open original vacancy ↗</a>
        <h3>Match evidence</h3>
        <p>Experience {j.detail.experience_years ?? 'unknown'} · Deadline {j.detail.deadline ?? 'unknown'}</p>
        {['architecture','automation','security','experience','location_compensation'].map(k=>j.detail[k]&&<p key={k}>{k.replaceAll('_',' ')} {j.detail[k].awarded}/{j.detail[k].weight}<br/>{j.detail[k].requirement||'No verified match'}<br/>{j.detail[k].evidence}</p>)}
        <h3>Sponsorship evidence</h3>{j.sponsor_register && <p>Registry match for {j.sponsor_entity} · {j.sponsor_register_route} · {j.sponsor_register_detail} · {format(j.sponsor_register_checked)}. <a href={j.sponsor_register} target="_blank" rel="noreferrer">Official register ↗</a></p>}{j.evidence&&<p>{j.evidence} <a href={j.evidence_source} target="_blank" rel="noreferrer">Source ↗</a> · {format(j.verified)}</p>}
        {j.evidence_candidates.length>0&&<ul>{j.evidence_candidates.map((x,i)=><li key={i}>{x}</li>)}</ul>}
        <form onSubmit={e=>{e.preventDefault();const f=new FormData(e.currentTarget); const flag=k=>f.get(k)===''?null:f.get(k)==='yes';
          action(()=>api('/jobs/'+j.id+'/sponsorship','PUT',{status:f.get('status'),source:f.get('source'),evidence:f.get('evidence'),route:f.get('route')||null,
            india_considered:flag('india'),relocation:flag('relocation')}),'Evidence saved');}}>
          <label>Status<select name="status" defaultValue={j.status||'unknown'}>{Object.entries(statuses).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
          <label>Source URL<input name="source" type="url" defaultValue={j.evidence_source||j.url} required/></label>
          <label>Evidence excerpt<textarea name="evidence" defaultValue={j.evidence||''} minLength={5} required/></label>
          <label>Visa route<input name="route" defaultValue={j.route||''}/></label>
          {[['india','Applicants residing in India considered',j.india_considered],['relocation','Relocation offered',j.relocation]].map(([name,label,value])=><label key={name}>{label}<select name={name} defaultValue={value==null?'':value?'yes':'no'}><option value="">Unknown</option><option value="yes">Yes</option><option value="no">No</option></select></label>)}
          <button>Save evidence</button></form>
        <div className="toolbar"><button disabled={!j.eligible} onClick={()=>action(()=>api('/jobs/'+j.id+'/prepare','POST'),'Application prepared')}>Prepare application</button>
        <button className="minor" onClick={()=>action(()=>api('/employers/'+encodeURIComponent(j.employer)+'/exclude','PUT'),'Employer excluded')}>Exclude employer</button></div>
        <details><summary>Original job description</summary><p>{j.description}</p></details></>;})()}
      {dialog.type==='batch' && <><h2>{dialog.decision==='approved'?'Approve':'Reject'} {selected.length} applications</h2><ul>{selected.map(d=><li key={d.vacancy_id}>{d.employer} · {d.title}</li>)}</ul>
        <p>Approved drafts are ready for manual submission through the employer’s form.</p><button onClick={()=>action(()=>api('/decisions','POST',{decision:dialog.decision,items:selected.map(d=>({vacancy_id:d.vacancy_id,revision:d.revision}))}),'Decision recorded')}>Confirm decision</button></>}
      {dialog.type==='draft' && (()=>{const d=dialog.draft;return <><h2>{d.title}</h2><p>{d.employer} · {d.country}</p>
        <details><summary>Tailored CV</summary><pre>{d.cv}</pre><button className="minor" onClick={()=>exportFile('tailored-cv.txt',d.cv)}>Download CV</button></details>
        <details><summary>Cover letter</summary><pre>{d.letter}</pre><button className="minor" onClick={()=>exportFile('cover-letter.txt',d.letter)}>Download letter</button></details>
        <h3>Application answers</h3><form onSubmit={e=>{e.preventDefault();const f=new FormData(e.currentTarget);
          action(()=>api('/drafts/'+d.vacancy_id+'/answers','PUT',{revision:d.revision,answers:JSON.parse(f.get('answers'))}),'Answers saved');}}>
          <textarea name="answers" aria-label="Application answers JSON" rows={6} defaultValue={JSON.stringify(d.answers,null,2)} readOnly={!['review','approved'].includes(d.state)}/>
          {['review','approved'].includes(d.state)&&<button>Save answers</button>}</form><ul>{d.unresolved.map(x=><li key={x}>{x}</li>)}</ul>
        {d.state==='approved'&&<><a href={d.url} target="_blank" rel="noreferrer">Open employer form ↗</a>
        <form onSubmit={e=>{e.preventDefault();const f=new FormData(e.currentTarget);action(()=>api('/submissions/'+d.vacancy_id,'POST',{reference:f.get('reference')}),'Candidate-reported confirmation saved');}}>
          <label>Employer confirmation reference<input name="reference" minLength={5} required/></label><button>Record submitted application</button></form></>}</>;})()}
      {dialog.type==='document' && <><h2>Document preview</h2><pre>{dialog.text}</pre><button onClick={()=>exportFile('application-document.txt',dialog.text)}>Download</button></>}
      {dialog.type==='track'&&<><h2>Record application update</h2><form onSubmit={e=>{e.preventDefault();const f=new FormData(e.currentTarget);action(()=>api('/tracking/'+dialog.id,'POST',{status:f.get('status'),description:f.get('description'),event_time:new Date(f.get('date')).toISOString()}),'Update recorded');}}>
        <label>Event<select name="status">{['recruiter_response','interview','rejected','follow_up','offer'].map(s=><option value={s} key={s}>{s.replaceAll('_',' ')}</option>)}</select></label>
        <label>Details<textarea name="description" required maxLength={2000}/></label><label>Date and time<input type="datetime-local" name="date" required/></label><button>Save update</button></form></>}
    </section></div>}
  </div>;
}
createRoot(document.getElementById('root')).render(<App/>);
