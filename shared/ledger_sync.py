"""
账本同步和验证系统 (Ledger Sync & Verification)

当用户连接到 Tracker 时，提交账本证明
Tracker 验证账本签名，确保积分真实性
"""

import json
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
import time


@dataclass
class LedgerProof:
    """账本证明"""
    fingerprint: str  # 硬件指纹
    total_points: int  # 总积分
    ledger_hash: str  # 账本整体哈希
    last_entry_timestamp: int  # 最后一条记录的时间戳
    entries_count: int  # 账本条目数
    signature: str  # 用户签名


class LedgerValidator:
    """
    账本验证器（Tracker 端）
    
    验证用户提交的账本证明
    """
    
    @staticmethod
    def validate_ledger_proof(proof: LedgerProof, peer_public_key: str) -> Tuple[bool, str]:
        """
        验证账本证明
        
        Args:
            proof: 用户提交的账本证明
            peer_public_key: 用户的公钥
            
        Returns:
            (valid, reason): 是否有效 + 原因
        """
        # 1. 检查积分范围（防止溢出）
        if proof.total_points < 0 or proof.total_points > 1000000:
            return False, "积分超出合理范围"
        
        # 2. 检查时间戳（不能超过当前时间）
        if proof.last_entry_timestamp > int(time.time()):
            return False, "时间戳不合理（未来）"
        
        # 3. 检查账本条目数（合理范围）
        if proof.entries_count < 0 or proof.entries_count > 100000:
            return False, "账本条目数不合理"
        
        # 4. 检查是否为已知恶意地址（黑名单）
        if LedgerValidator._is_blacklisted(proof.fingerprint):
            return False, "用户已被列入黑名单"
        
        # 5. 签名验证（如果有公钥）
        if peer_public_key:
            # TODO: 使用 ED25519 验证签名
            pass
        
        return True, "账本有效"
    
    @staticmethod
    def _is_blacklisted(fingerprint: str) -> bool:
        """检查是否在黑名单中"""
        # TODO: 从数据库查询黑名单
        return False
    
    @staticmethod
    def calculate_reputation_score(
        total_points: int,
        entries_count: int,
        last_activity_days_ago: int,
    ) -> int:
        """
        计算用户信誉分数
        
        Args:
            total_points: 总积分
            entries_count: 账本条目数
            last_activity_days_ago: 距离最后活动多少天
            
        Returns:
            int: 信誉分数 (0-100)
        """
        score = 50  # 基础分
        
        # 积分权重
        score += min(total_points // 100, 30)
        
        # 活跃度权重
        if last_activity_days_ago == 0:
            score += 15
        elif last_activity_days_ago <= 7:
            score += 10
        elif last_activity_days_ago <= 30:
            score += 5
        
        # 交易历史权重
        score += min(entries_count // 10, 20)
        
        return min(score, 100)


class LedgerSyncManager:
    """
    账本同步管理器（客户端）
    
    处理与 Tracker 的同步，生成证明
    """
    
    def __init__(self, local_ledger_path: str):
        """
        Args:
            local_ledger_path: 本地账本文件路径
        """
        self.ledger_file = Path(local_ledger_path)
    
    def generate_proof(self) -> Optional[LedgerProof]:
        """
        生成账本证明
        
        从本地账本生成可提交到 Tracker 的证明
        """
        if not self.ledger_file.exists():
            return None
        
        # 读取账本
        records = []
        with open(self.ledger_file, 'r') as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))
        
        if not records:
            return None
        
        # 计算总积分
        total_points = sum(
            r['transaction'].get('amount', 0) 
            for r in records
        )
        
        # 计算账本哈希
        ledger_hash = hashlib.sha256(
            json.dumps(records, sort_keys=True).encode()
        ).hexdigest()
        
        # 获取最后一条记录的时间戳
        last_entry_timestamp = records[-1].get('timestamp', int(time.time()))
        
        # 构建证明
        proof_data = {
            "total_points": total_points,
            "ledger_hash": ledger_hash,
            "last_entry_timestamp": last_entry_timestamp,
            "entries_count": len(records),
        }
        
        # 这里应该用私钥签名，简化版直接用哈希
        signature = hashlib.sha256(
            json.dumps(proof_data, sort_keys=True).encode()
        ).hexdigest()
        
        return LedgerProof(
            fingerprint=records[0]['identity'],  # 从第一条记录获取指纹
            total_points=total_points,
            ledger_hash=ledger_hash,
            last_entry_timestamp=last_entry_timestamp,
            entries_count=len(records),
            signature=signature,
        )
    
    def submit_proof_to_tracker(self, tracker_url: str, proof: LedgerProof) -> Dict:
        """
        提交账本证明到 Tracker
        
        Args:
            tracker_url: Tracker 服务器地址
            proof: 账本证明
            
        Returns:
            Dict: Tracker 的响应
        """
        import requests
        
        payload = {
            "fingerprint": proof.fingerprint,
            "total_points": proof.total_points,
            "ledger_hash": proof.ledger_hash,
            "last_entry_timestamp": proof.last_entry_timestamp,
            "entries_count": proof.entries_count,
            "signature": proof.signature,
        }
        
        try:
            response = requests.post(
                f"{tracker_url}/api/users/submit-ledger",
                json=payload,
                timeout=10,
            )
            return response.json()
        except Exception as e:
            return {
                "status": "error",
                "reason": str(e),
            }


class OfflineLedger:
    """
    离线模式账本
    
    用户离线时，所有交易记录在本地
    重新连接时自动同步到 Tracker
    """
    
    def __init__(self, ledger_file: str, cache_dir: str):
        """
        Args:
            ledger_file: 账本文件路径
            cache_dir: 缓存目录（存储待同步的交易）
        """
        self.ledger_file = Path(ledger_file)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.pending_sync = self.cache_dir / "pending_sync.jsonl"
        self.synced_history = self.cache_dir / "synced_history.jsonl"
    
    def record_offline_transaction(self, transaction: Dict) -> None:
        """
        记录离线交易
        
        Args:
            transaction: 交易数据
        """
        record = {
            "timestamp": int(time.time()),
            "transaction": transaction,
            "synced": False,
        }
        
        with open(self.pending_sync, 'a') as f:
            f.write(json.dumps(record) + '\n')
    
    def get_pending_sync(self) -> List[Dict]:
        """获取待同步的交易"""
        if not self.pending_sync.exists():
            return []
        
        pending = []
        with open(self.pending_sync, 'r') as f:
            for line in f:
                if line.strip():
                    record = json.loads(line)
                    if not record.get('synced'):
                        pending.append(record)
        
        return pending
    
    def mark_synced(self, transaction_ids: List[int]) -> None:
        """
        标记交易已同步
        
        Args:
            transaction_ids: 已同步的交易 ID 列表
        """
        # 这是简化实现，实际应该用更稳健的方法
        pending = self.get_pending_sync()
        
        with open(self.pending_sync, 'w') as f:
            for i, record in enumerate(pending):
                if i in transaction_ids:
                    record['synced'] = True
                    # 写入已同步历史
                    with open(self.synced_history, 'a') as h:
                        h.write(json.dumps(record) + '\n')
                else:
                    f.write(json.dumps(record) + '\n')


# ==================== 使用示例 ====================

if __name__ == "__main__":
    print("=== 账本验证和同步系统测试 ===\n")
    
    # 1. 创建示例账本证明
    print("1. 创建示例账本证明:")
    
    proof = LedgerProof(
        fingerprint="abc123def456",
        total_points=500,
        ledger_hash="sha256_hash_here",
        last_entry_timestamp=int(time.time()),
        entries_count=15,
        signature="signature_here",
    )
    
    print(f"   用户指纹: {proof.fingerprint}")
    print(f"   总积分: {proof.total_points}")
    print(f"   账本条目: {proof.entries_count}")
    
    # 2. 验证账本证明
    print("\n2. 验证账本证明 (Tracker 端):")
    
    valid, reason = LedgerValidator.validate_ledger_proof(proof, None)
    print(f"   有效: {valid}")
    print(f"   原因: {reason}")
    
    # 3. 计算信誉分数
    print("\n3. 计算用户信誉分数:")
    
    reputation = LedgerValidator.calculate_reputation_score(
        total_points=500,
        entries_count=15,
        last_activity_days_ago=2,
    )
    print(f"   信誉分数: {reputation}/100")
    
    # 4. 离线账本测试
    print("\n4. 离线模式账本:")
    
    offline_ledger = OfflineLedger(
        ledger_file="/tmp/ledger.jsonl",
        cache_dir="/tmp/ledger_cache",
    )
    
    # 记录离线交易
    offline_ledger.record_offline_transaction({
        "type": "download",
        "asset_id": "asset_123",
        "amount": 0,
    })
    print(f"   已记录 1 条离线交易")
    
    # 获取待同步
    pending = offline_ledger.get_pending_sync()
    print(f"   待同步交易数: {len(pending)}")
