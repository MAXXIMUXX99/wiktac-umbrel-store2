from fastapi import FastAPI
app=FastAPI()
@app.get('/api/state')
def s(): return {'ok': True}
@app.get('/')
def r(): return 'ok'
