import hashlib, json, platform, subprocess, uuid

def get_system_info():
    info = {}
    if platform.system() == 'Windows':
        try:
            r = subprocess.check_output('wmic cpu get processorid', shell=True, text=True).strip()
            info['cpu_id'] = r.split('\n')[-1].strip() if r else 'unknown'
        except: info['cpu_id'] = 'unknown'
        try:
            r = subprocess.check_output('wmic logicaldisk get volumeserialnumber', shell=True, text=True).strip()
            info['disk_serial'] = r.split('\n')[-1].strip() if r else 'unknown'
        except: info['disk_serial'] = 'unknown'
        try:
            r = subprocess.check_output('wmic baseboard get serialnumber', shell=True, text=True).strip()
            info['motherboard'] = r.split('\n')[-1].strip() if r else 'unknown'
        except: info['motherboard'] = 'unknown'
    info['mac'] = format(uuid.getnode(), '012x')
    return info

info = get_system_info()
print('=== 硬件信息 ===')
for k, v in info.items():
    print(f'  {k}: {v}')

data = json.dumps(info, sort_keys=True)
fp = hashlib.sha256(data.encode()).hexdigest()
print(f'\n=== 生成的硬件指纹 ===')
print(f'  指纹: {fp}')

first_byte = int(fp[:2], 16)
animals = ['phoenix','dragon','tiger','crane','tortoise','whale','eagle','panda']
name = f'{animals[first_byte % len(animals)]}_{fp[:6]}'
print(f'  昵称: {name}')
print(f'  PeerID: -BD0001-{fp[:12]}')
