"""
硬件指纹生成系统 (Hardware Fingerprint System)

用于生成唯一的去中心化身份，不依赖中央服务器
基于本地硬件信息，确保一台电脑一个身份
"""

import hashlib
import json
import uuid
import platform
import subprocess
from typing import Dict, Optional
from pathlib import Path


class HardwareFingerprint:
    """生成硬件指纹身份"""
    
    @staticmethod
    def get_system_info() -> Dict[str, str]:
        """收集系统硬件信息"""
        info = {}
        
        # 1. CPU 信息
        try:
            if platform.system() == "Windows":
                result = subprocess.check_output(
                    "wmic cpu get processorid", 
                    shell=True, 
                    text=True
                ).strip()
                info["cpu_id"] = result.split('\n')[-1] if result else "unknown"
            else:
                result = subprocess.check_output(
                    "cat /proc/cpuinfo | grep Serial",
                    shell=True, text=True
                ).strip()
                info["cpu_id"] = result or "unknown"
        except:
            info["cpu_id"] = "unknown"
        
        # 2. 磁盘序列号
        try:
            if platform.system() == "Windows":
                result = subprocess.check_output(
                    "wmic logicaldisk get volumeserialnumber",
                    shell=True, text=True
                ).strip()
                info["disk_serial"] = result.split('\n')[-1] if result else "unknown"
            else:
                result = subprocess.check_output(
                    "lsblk -d -n -o SERIAL /dev/sda",
                    shell=True, text=True
                ).strip()
                info["disk_serial"] = result or "unknown"
        except:
            info["disk_serial"] = "unknown"
        
        # 3. 主板信息
        try:
            if platform.system() == "Windows":
                result = subprocess.check_output(
                    "wmic baseboard get serialnumber",
                    shell=True, text=True
                ).strip()
                info["motherboard_serial"] = result.split('\n')[-1] if result else "unknown"
            else:
                result = subprocess.check_output(
                    "dmidecode -t 2 | grep Serial",
                    shell=True, text=True
                ).strip()
                info["motherboard_serial"] = result or "unknown"
        except:
            info["motherboard_serial"] = "unknown"
        
        # 4. 系统和架构
        info["system"] = platform.system()
        info["machine"] = platform.machine()
        info["processor"] = platform.processor()
        
        # 5. Mac 地址（网卡）
        try:
            mac = uuid.getnode()
            info["mac_address"] = format(mac, '012x')
        except:
            info["mac_address"] = "unknown"
        
        return info
    
    @staticmethod
    def generate_fingerprint() -> str:
        """
        生成唯一硬件指纹 (SHA256)
        
        Returns:
            str: 64 字符十六进制哈希值
        """
        info = HardwareFingerprint.get_system_info()
        
        # 按固定顺序排列（确保同一硬件生成相同指纹）
        fingerprint_data = json.dumps({
            "cpu_id": info.get("cpu_id", ""),
            "disk_serial": info.get("disk_serial", ""),
            "motherboard_serial": info.get("motherboard_serial", ""),
            "mac_address": info.get("mac_address", ""),
        }, sort_keys=True)
        
        # SHA256 哈希
        return hashlib.sha256(fingerprint_data.encode()).hexdigest()
    
    @staticmethod
    def generate_peer_id(fingerprint: str) -> str:
        """
        生成 P2P Peer ID（用于 BitTorrent 网络）
        
        Args:
            fingerprint: 硬件指纹
            
        Returns:
            str: 20 字节 Peer ID (hex 编码)
        """
        # BitTorrent Peer ID 格式: "-XX####-" + 12 随机字符
        # 这里用指纹前 12 字符替代随机数
        prefix = "-BD0001-"  # BD = Blender Decentralized
        suffix = fingerprint[:12]
        return (prefix + suffix).encode().hex()[:40]  # 20 bytes = 40 hex chars
    
    @staticmethod
    def generate_identity(name_hint: Optional[str] = None) -> Dict[str, str]:
        """
        生成完整身份信息
        
        Args:
            name_hint: 用户提示名称（可选）
            
        Returns:
            Dict 包含:
                - fingerprint: 硬件指纹 (64 hex)
                - peer_id: P2P ID (40 hex)
                - public_key: 用于签名的公钥 (hex)
                - identity_name: 身份显示名称
                - created_timestamp: 生成时间戳
        """
        fingerprint = HardwareFingerprint.generate_fingerprint()
        peer_id = HardwareFingerprint.generate_peer_id(fingerprint)
        
        # 生成 ED25519 密钥对（用于签名）
        from cryptography.hazmat.primitives.asymmetric import ed25519
        private_key = ed25519.Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        
        public_key_hex = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        ).hex()
        
        # 生成友好名称
        if name_hint:
            identity_name = f"{name_hint}@{fingerprint[:8]}"
        else:
            # 从指纹生成昵称
            first_byte = int(fingerprint[:2], 16)
            animals = ["phoenix", "dragon", "tiger", "crane", "tortoise", "whale", "eagle", "panda"]
            animal = animals[first_byte % len(animals)]
            identity_name = f"{animal}_{fingerprint[:6]}"
        
        return {
            "fingerprint": fingerprint,
            "peer_id": peer_id,
            "public_key": public_key_hex,
            "identity_name": identity_name,
            "created_timestamp": str(int(__import__('time').time())),
        }


# ==================== 账本系统 ====================

class LocalLedger:
    """
    本地账本系统
    
    记录用户的所有贡献数据（离线模式）
    - 下载记录
    - 做种记录
    - 上传记录
    - 积分余额
    - 交易历史
    """
    
    def __init__(self, ledger_dir: Optional[str] = None):
        """
        初始化账本
        
        Args:
            ledger_dir: 账本存储目录（默认 ~/.blender/blendlink_ledger/）
        """
        if ledger_dir is None:
            # 使用 Blender 标准目录
            if platform.system() == "Windows":
                ledger_dir = Path.home() / "AppData" / "Roaming" / "Blender" / "blendlink_ledger"
            else:
                ledger_dir = Path.home() / ".blender" / "blendlink_ledger"
        
        self.ledger_dir = Path(ledger_dir)
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        
        # 账本文件
        self.ledger_file = self.ledger_dir / "ledger.jsonl"  # JSON Lines format
        self.balance_file = self.ledger_dir / "balance.json"
        self.identity_file = self.ledger_dir / "identity.json"
        
        # 初始化或加载
        self._init_identity()
        self._load_or_init_balance()
    
    def _init_identity(self):
        """初始化或加载身份"""
        if self.identity_file.exists():
            with open(self.identity_file, 'r') as f:
                self.identity = json.load(f)
        else:
            self.identity = HardwareFingerprint.generate_identity()
            self._save_identity()
    
    def _save_identity(self):
        """保存身份到本地"""
        with open(self.identity_file, 'w') as f:
            json.dump(self.identity, f, indent=2)
    
    def _load_or_init_balance(self):
        """加载或初始化余额"""
        if self.balance_file.exists():
            with open(self.balance_file, 'r') as f:
                self.balance = json.load(f)
        else:
            self.balance = {
                "total_points": 0,
                "available_points": 0,
                "locked_points": 0,  # 做种中的资产锁定积分
                "lifetime_earned": 0,
                "lifetime_spent": 0,
            }
            self._save_balance()
    
    def _save_balance(self):
        """保存余额到本地"""
        with open(self.balance_file, 'w') as f:
            json.dump(self.balance, f, indent=2)
    
    def record_transaction(self, transaction: Dict) -> Dict:
        """
        记录交易
        
        Args:
            transaction: {
                "type": "download|upload|seed|like",
                "asset_id": "sha256_hash",
                "asset_name": "资产名称",
                "amount": 100,  # 积分增减
                "metadata": {...}
            }
            
        Returns:
            Dict: 已签名的交易记录
        """
        timestamp = int(__import__('time').time())
        
        record = {
            "timestamp": timestamp,
            "identity": self.identity["fingerprint"],
            "transaction": transaction,
            "balance_before": self.balance["total_points"],
        }
        
        # 签名交易
        record["signature"] = self._sign_transaction(record)
        
        # 追加到账本
        with open(self.ledger_file, 'a') as f:
            f.write(json.dumps(record) + '\n')
        
        # 更新余额
        self.balance["total_points"] += transaction.get("amount", 0)
        self.balance["lifetime_earned"] += max(0, transaction.get("amount", 0))
        self.balance["lifetime_spent"] += max(0, -transaction.get("amount", 0))
        self._save_balance()
        
        record["balance_after"] = self.balance["total_points"]
        return record
    
    def _sign_transaction(self, record: Dict) -> str:
        """
        对交易进行签名（使用硬件指纹）
        
        实际使用时，应使用 ED25519 私钥
        这里简化版使用 SHA256(record + secret)
        """
        # TODO: 使用真实 ED25519 签名
        record_str = json.dumps(record, sort_keys=True)
        signature = hashlib.sha256(
            (record_str + self.identity["fingerprint"]).encode()
        ).hexdigest()
        return signature
    
    def get_balance(self) -> Dict:
        """获取当前余额"""
        return self.balance.copy()
    
    def get_ledger_history(self, limit: Optional[int] = None) -> list:
        """
        获取账本历史
        
        Args:
            limit: 返回最后 N 条记录（None = 全部）
            
        Returns:
            list: 交易记录列表
        """
        if not self.ledger_file.exists():
            return []
        
        records = []
        with open(self.ledger_file, 'r') as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))
        
        if limit:
            return records[-limit:]
        return records
    
    def get_identity_info(self) -> Dict:
        """获取身份信息"""
        return self.identity.copy()
    
    def export_ledger_summary(self) -> Dict:
        """导出账本摘要报告"""
        return {
            "identity": self.get_identity_info(),
            "balance": self.get_balance(),
            "ledger_entries": len(self.get_ledger_history()),
            "ledger_file": str(self.ledger_file),
        }


# ==================== 导入修复 ====================

try:
    from cryptography.hazmat.primitives import serialization
except ImportError:
    # 如果没有安装 cryptography，使用简化版
    serialization = None
    print("Warning: cryptography not installed. Using simplified fingerprint generation.")


if __name__ == "__main__":
    # 测试硬件指纹生成
    print("=== 硬件指纹生成测试 ===")
    print("\n1. 系统硬件信息:")
    info = HardwareFingerprint.get_system_info()
    for key, value in info.items():
        print(f"   {key}: {value}")
    
    print("\n2. 生成的身份:")
    identity = HardwareFingerprint.generate_identity(name_hint="BlockArtist")
    for key, value in identity.items():
        print(f"   {key}: {value}")
    
    print("\n3. 本地账本测试:")
    ledger = LocalLedger()
    print(f"   身份名称: {ledger.identity['identity_name']}")
    print(f"   指纹: {ledger.identity['fingerprint'][:16]}...")
    
    # 记录一些示例交易
    print("\n4. 记录示例交易:")
    
    # 上传资产
    tx1 = ledger.record_transaction({
        "type": "upload",
        "asset_id": "sha256_abcd1234",
        "asset_name": "High-Poly Dragon Model",
        "amount": 50,
        "metadata": {
            "file_size": 250000000,  # 250MB
            "category": "model",
        }
    })
    print(f"   上传: +50 积分")
    
    # 下载资产
    tx2 = ledger.record_transaction({
        "type": "download",
        "asset_id": "sha256_xyz789",
        "asset_name": "PBR Material Pack",
        "amount": 0,  # 下载不扣积分
        "metadata": {
            "requires_seeding_hours": 24,
        }
    })
    print(f"   下载: 需做种 24 小时")
    
    # 完成做种
    tx3 = ledger.record_transaction({
        "type": "seed",
        "asset_id": "sha256_xyz789",
        "asset_name": "PBR Material Pack",
        "amount": 30,  # 做种 24 小时获得 30 积分
        "metadata": {
            "seed_duration_hours": 24,
            "bytes_uploaded": 52428800,  # 50MB
        }
    })
    print(f"   做种完成: +30 积分")
    
    print("\n5. 账本摘要:")
    summary = ledger.export_ledger_summary()
    print(f"   当前积分: {summary['balance']['total_points']}")
    print(f"   总累计: {summary['balance']['lifetime_earned']}")
    print(f"   账本条目数: {summary['ledger_entries']}")
    
    print("\n6. 交易历史:")
    for i, record in enumerate(ledger.get_ledger_history()[-3:], 1):
        tx = record['transaction']
        print(f"   {i}. {tx['type']}: {tx['asset_name']} ({tx['amount']:+d} 积分)")
