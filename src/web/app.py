# server.py - Flask Backend for Minecraft Server Manager
from flask import Flask, request, jsonify, session
from flask_cors import CORS
import subprocess
import os
import json
import threading
import time
import requests
import shutil
from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this'
CORS(app, supports_credentials=True)

# Configuration file
CONFIG_FILE = 'server_config.json'
SERVER_DIR = 'minecraft_servers'

# Default configuration
default_config = {
    "admin_username": "admin",
    "admin_password_hash": generate_password_hash("admin123"),
    "ftp_port": 8021,
    "ftp_username": "ftpuser",
    "ftp_password": "ftppass",
    "server_ip": "0.0.0.0",
    "server_port": 25565,
    "dns_hostname": "mc.yourserver.com",
    "max_memory": "4G",
    "min_memory": "2G"
}

# Global variables
minecraft_processes = {}
server_logs = {}

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    else:
        save_config(default_config)
        return default_config

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)

config = load_config()

# Create server directory
os.makedirs(SERVER_DIR, exist_ok=True)

# Login endpoint
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if username == config['admin_username'] and check_password_hash(config['admin_password_hash'], password):
        session['logged_in'] = True
        session['username'] = username
        return jsonify({'success': True, 'message': 'Login successful'})
    return jsonify({'success': False, 'message': 'Invalid credentials'}), 401

# Logout endpoint
@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

# Check authentication
def require_auth(f):
    def wrapper(*args, **kwargs):
        if not session.get('logged_in'):
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

# Get settings
@app.route('/api/settings', methods=['GET'])
@require_auth
def get_settings():
    safe_config = {k: v for k, v in config.items() if k != 'admin_password_hash'}
    return jsonify(safe_config)

# Update settings
@app.route('/api/settings', methods=['POST'])
@require_auth
def update_settings():
    global config
    data = request.json
    
    # Update config
    for key in ['ftp_port', 'ftp_username', 'ftp_password', 'server_ip', 
                'server_port', 'dns_hostname', 'max_memory', 'min_memory']:
        if key in data:
            config[key] = data[key]
    
    # Update admin credentials if provided
    if 'admin_username' in data:
        config['admin_username'] = data['admin_username']
    if 'new_password' in data and data['new_password']:
        config['admin_password_hash'] = generate_password_hash(data['new_password'])
    
    save_config(config)
    return jsonify({'success': True, 'message': 'Settings updated'})

# Download server software
@app.route('/api/download', methods=['POST'])
@require_auth
def download_server():
    data = request.json
    server_type = data.get('type')
    version = data.get('version')
    server_name = data.get('name', f'{server_type}_{version}')
    
    server_path = os.path.join(SERVER_DIR, server_name)
    os.makedirs(server_path, exist_ok=True)
    
    try:
        jar_path = os.path.join(server_path, 'server.jar')
        
        # Download URLs for different server types
        if server_type == 'paper':
            # Get latest build
            builds_url = f'https://api.papermc.io/v2/projects/paper/versions/{version}'
            response = requests.get(builds_url)
            latest_build = response.json()['builds'][-1]
            download_url = f'https://api.papermc.io/v2/projects/paper/versions/{version}/builds/{latest_build}/downloads/paper-{version}-{latest_build}.jar'
        
        elif server_type == 'purpur':
            download_url = f'https://api.purpurmc.org/v2/purpur/{version}/latest/download'
        
        elif server_type == 'fabric':
            # Download fabric installer
            download_url = f'https://meta.fabricmc.net/v2/versions/loader/{version}/stable/1.0.0/server/jar'
        
        elif server_type == 'vanilla':
            # Get vanilla server
            manifest_url = 'https://launchermeta.mojang.com/mc/game/version_manifest.json'
            manifest = requests.get(manifest_url).json()
            version_data = next((v for v in manifest['versions'] if v['id'] == version), None)
            if version_data:
                version_json = requests.get(version_data['url']).json()
                download_url = version_json['downloads']['server']['url']
        
        elif server_type == 'forge':
            download_url = f'https://maven.minecraftforge.net/net/minecraftforge/forge/{version}/forge-{version}-installer.jar'
        
        elif server_type == 'neoforge':
            download_url = f'https://maven.neoforged.net/releases/net/neoforged/forge/{version}/forge-{version}-installer.jar'
        
        elif server_type == 'spigot':
            return jsonify({'success': False, 'message': 'Spigot requires BuildTools. Use Paper instead.'})
        
        else:
            return jsonify({'success': False, 'message': 'Unknown server type'})
        
        # Download the jar
        response = requests.get(download_url, stream=True)
        with open(jar_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        # Create eula.txt
        with open(os.path.join(server_path, 'eula.txt'), 'w') as f:
            f.write('eula=true')
        
        # Create server.properties
        properties = f'''server-port={config['server_port']}
motd=Minecraft Server - {server_name}
max-players=20
online-mode=true
difficulty=normal
gamemode=survival
'''
        with open(os.path.join(server_path, 'server.properties'), 'w') as f:
            f.write(properties)
        
        return jsonify({'success': True, 'message': f'{server_type} {version} downloaded', 'server_name': server_name})
    
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# List servers
@app.route('/api/servers', methods=['GET'])
@require_auth
def list_servers():
    servers = []
    if os.path.exists(SERVER_DIR):
        for server_name in os.listdir(SERVER_DIR):
            server_path = os.path.join(SERVER_DIR, server_name)
            if os.path.isdir(server_path):
                status = 'online' if server_name in minecraft_processes else 'offline'
                servers.append({
                    'name': server_name,
                    'status': status,
                    'path': server_path
                })
    return jsonify(servers)

# Start server
@app.route('/api/start', methods=['POST'])
@require_auth
def start_server():
    data = request.json
    server_name = data.get('server_name')
    
    if server_name in minecraft_processes:
        return jsonify({'success': False, 'message': 'Server already running'})
    
    server_path = os.path.join(SERVER_DIR, server_name)
    jar_path = os.path.join(server_path, 'server.jar')
    
    if not os.path.exists(jar_path):
        return jsonify({'success': False, 'message': 'Server jar not found'})
    
    try:
        # Start Minecraft server process
        process = subprocess.Popen(
            ['java', f'-Xmx{config["max_memory"]}', f'-Xms{config["min_memory"]}', 
             '-jar', 'server.jar', 'nogui'],
            cwd=server_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1
        )
        
        minecraft_processes[server_name] = process
        server_logs[server_name] = []
        
        # Thread to read logs
        def read_logs():
            for line in process.stdout:
                if server_name in server_logs:
                    server_logs[server_name].append(line.strip())
                    if len(server_logs[server_name]) > 1000:
                        server_logs[server_name].pop(0)
        
        threading.Thread(target=read_logs, daemon=True).start()
        
        return jsonify({'success': True, 'message': 'Server started'})
    
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# Stop server
@app.route('/api/stop', methods=['POST'])
@require_auth
def stop_server():
    data = request.json
    server_name = data.get('server_name')
    
    if server_name not in minecraft_processes:
        return jsonify({'success': False, 'message': 'Server not running'})
    
    try:
        process = minecraft_processes[server_name]
        process.stdin.write('stop\n')
        process.stdin.flush()
        process.wait(timeout=30)
        del minecraft_processes[server_name]
        return jsonify({'success': True, 'message': 'Server stopped'})
    except Exception as e:
        process.terminate()
        del minecraft_processes[server_name]
        return jsonify({'success': True, 'message': 'Server force stopped'})

# Get server status
@app.route('/api/status', methods=['GET'])
@require_auth
def get_status():
    server_name = request.args.get('server_name')
    
    if server_name in minecraft_processes:
        process = minecraft_processes[server_name]
        return jsonify({
            'status': 'online' if process.poll() is None else 'offline',
            'logs': server_logs.get(server_name, [])[-100:]  # Last 100 lines
        })
    
    return jsonify({'status': 'offline', 'logs': []})

# Send command to server
@app.route('/api/command', methods=['POST'])
@require_auth
def send_command():
    data = request.json
    server_name = data.get('server_name')
    command = data.get('command')
    
    if server_name not in minecraft_processes:
        return jsonify({'success': False, 'message': 'Server not running'})
    
    try:
        process = minecraft_processes[server_name]
        process.stdin.write(command + '\n')
        process.stdin.flush()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# File management endpoints
@app.route('/api/files', methods=['GET'])
@require_auth
def list_files():
    server_name = request.args.get('server_name')
    path = request.args.get('path', '')
    
    server_path = os.path.join(SERVER_DIR, server_name, path)
    
    if not os.path.exists(server_path):
        return jsonify({'error': 'Path not found'}), 404
    
    files = []
    for item in os.listdir(server_path):
        item_path = os.path.join(server_path, item)
        files.append({
            'name': item,
            'type': 'folder' if os.path.isdir(item_path) else 'file',
            'size': os.path.getsize(item_path) if os.path.isfile(item_path) else 0
        })
    
    return jsonify(files)

@app.route('/api/files/upload', methods=['POST'])
@require_auth
def upload_file():
    server_name = request.form.get('server_name')
    path = request.form.get('path', '')
    file = request.files.get('file')
    
    if not file:
        return jsonify({'error': 'No file provided'}), 400
    
    server_path = os.path.join(SERVER_DIR, server_name, path)
    os.makedirs(server_path, exist_ok=True)
    
    file_path = os.path.join(server_path, file.filename)
    file.save(file_path)
    
    return jsonify({'success': True, 'message': 'File uploaded'})

@app.route('/api/files/delete', methods=['POST'])
@require_auth
def delete_file():
    data = request.json
    server_name = data.get('server_name')
    file_path = data.get('path')
    
    full_path = os.path.join(SERVER_DIR, server_name, file_path)
    
    if os.path.exists(full_path):
        if os.path.isdir(full_path):
            shutil.rmtree(full_path)
        else:
            os.remove(full_path)
        return jsonify({'success': True})
    
    return jsonify({'error': 'File not found'}), 404

if __name__ == '__main__':
    print("=" * 50)
    print("Minecraft Server Manager Starting...")
    print("=" * 50)
    print(f"Default Login: admin / admin123")
    print(f"Server IP: {config['server_ip']}")
    print(f"Web Port: 5000")
    print(f"Minecraft Port: {config['server_port']}")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=True)
