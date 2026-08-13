#!/usr/bin/env python
"""Cookbook: 注册自定义 Embedding Provider。

演示如何通过 @register_embedding_provider 装饰器注册一个自定义的
文本向量化 Provider，替换默认的 HashEmbedding。

本示例实现一个基于词频的 TF（Term Frequency）Embedding：
- 将文本按空格分词
- 构建固定维度的词频向量
- L2 归一化

运行方式:
    python examples/custom_embedding.py
    python examples/custom_embedding.py --help
"""
from __future__ import annotations

import argparse
import math
import re
import sys
import threading
from collections import Counter

# 确保能导入 agentbase
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

from agentbase.core.embeddings import embedding_registry


class TFEmbedding:
    """基于词频（Term Frequency）的 Embedding Provider。

    不依赖外部 API，纯 Python 实现，适合测试和教育目的。
    使用固定词汇表（256 个常见英文单词的 hash 映射）。
    """

    def __init__(self, dimension: int = 256) -> None:
        self._dimension = dimension
        self._lock = threading.Lock()

    @property
    def dimension(self) -> int:
        return self._dimension

    def _tokenize(self, text: str) -> list[str]:
        """简单分词：小写化 + 提取单词。"""
        return re.findall(r"[a-z0-9]+", text.lower())

    def embed(self, text: str) -> list[float]:
        tokens = self._tokenize(text)
        if not tokens:
            return [0.0] * self._dimension

        counter = Counter(tokens)
        vec = [0.0] * self._dimension

        for word, count in counter.items():
            # 将词 hash 到固定维度空间
            idx = hash(word) % self._dimension
            vec[idx] += count

        # L2 归一化
        magnitude = math.sqrt(sum(v * v for v in vec))
        if magnitude == 0:
            return vec
        return [v / magnitude for v in vec]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="注册并测试自定义 TF Embedding Provider",
    )
    parser.parse_args()

    print("=" * 60)
    print("Cookbook: 自定义 Embedding Provider")
    print("=" * 60)

    # 1. 注册前：查看已注册的 Provider
    print("\n1. 注册前已注册的 Provider:", embedding_registry.names())

    # 2. 注册自定义 Provider（手动注册实例）
    tf_provider = TFEmbedding()
    embedding_registry.register("tf", tf_provider, override=True)
    print("2. 已注册 'tf' Provider")

    # 3. 获取 Provider 实例
    provider = embedding_registry.get("tf")
    print(f"3. 获取 Provider: {provider.__class__.__name__}, dimension={provider.dimension}")

    # 4. 测试 embed
    text1 = "the quick brown fox jumps over the lazy dog"
    text2 = "a quick brown dog runs in the park"
    vec1 = provider.embed(text1)
    vec2 = provider.embed(text2)

    print("\n4. 测试 embed:")
    print(f"   文本 1: '{text1}'")
    print(f"   向量维度: {len(vec1)}")
    print(f"   前 5 个值: {vec1[:5]}")

    # 5. 计算余弦相似度
    dot = sum(a * b for a, b in zip(vec1, vec2))
    print(f"\n5. 余弦相似度('{text1}', '{text2}'): {dot:.4f}")

    # 6. 测试 batch embed
    batch = provider.embed_batch([text1, text2])
    print(f"\n6. batch embed: {len(batch)} 个向量, 维度={len(batch[0])}")

    # 7. 验证注册表状态
    print(f"\n7. 最终已注册 Provider: {embedding_registry.names()}")

    print("\n" + "=" * 60)
    print("[OK] 示例完成！在 config 中设置 embedding.provider: tf 即可使用")
    print("=" * 60)


if __name__ == "__main__":
    main()
