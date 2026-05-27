import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'Warehouse'))

from flask import Flask, jsonify, send_from_directory

from sim_setup import build_simulation

app = Flask(__name__, static_folder='static')

print('Building simulation...', flush=True)
_SIM = build_simulation()
print(f"Ready — navigate to http://localhost:5000", flush=True)


@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory(app.static_folder, filename)


@app.route('/api/simulation')
def simulation():
    return jsonify(_SIM)


if __name__ == '__main__':
    app.run(debug=False, port=5000)
