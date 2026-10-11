import sys; sys.path.insert(0, ".")
import freya, json
pid = 'acf56253-176d-418b-8378-b1621cb065f6'
s, i, st = freya.run_simulation(agent_id='cd24db51-32df-4d15-a117-24536ac92962', name='scenario1d', variables_by_persona={pid: {'customerNumber': '95004361', 'now': '2026-10-12T10:30:00+03:00'}}, max_seconds=300, concurrency=1)
it = freya.get_iteration(s, i)['iteration']
print('SIM', s, i, st, 'total', it.get('totalRuns'), 'passed', it.get('passedRuns'), 'failed', it.get('failedRuns'), 'errors', it.get('errorRuns'))
