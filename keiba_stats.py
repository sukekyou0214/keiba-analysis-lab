"""競馬分析ラボ：小さな集計処理（Ver.20.8.2）。UIや通信には依存しない。"""

def sample_count_for_metric(group, metric):
    """指標の分母となる観測走数。値が不明な走は数えない。"""
    column_map = {
        '勝率(%)': '着順_数値', '連対率(%)': '着順_数値',
        '複勝率(%)': '着順_数値', '平均着順': '着順_数値',
        '平均4角': '4角順位', '平均上がり': '上がり3F_秒',
        '上がり最速率(%)': '上がり順位',
        '上がり3位以内率(%)': '上がり順位',
        '平均上がり順位': '上がり順位',
    }
    if metric in column_map:
        return int(group[column_map[metric]].notna().sum())
    if metric in ('前方率(%)', '差し追込率(%)'):
        return int(group['推定脚質'].ne('不明').sum())
    return len(group)


def sample_label(n):
    if n == 0:
        return 'データなし'
    if n <= 2:
        return '参考（1〜2走）'
    if n <= 4:
        return '少数（3〜4走）'
    return '5走以上'


def summarize(group):
    finish = group['着順_数値'].dropna()
    agari = group['上がり順位'].dropna()
    def rate(count, denominator):
        return round(count / denominator * 100, 2) if denominator else float('nan')
    return {'出走数': len(group), '平均着順': finish.mean(), '平均4角': group['4角順位'].mean(), '平均上がり': group['上がり3F_秒'].mean(), '勝率(%)': rate((finish == 1).sum(), len(finish)), '連対率(%)': rate((finish <= 2).sum(), len(finish)), '複勝率(%)': rate((finish <= 3).sum(), len(finish)), '上がり対象走数': len(agari), '上がり最速率(%)': rate((agari == 1).sum(), len(agari)), '上がり3位以内率(%)': rate((agari <= 3).sum(), len(agari)), '平均上がり順位': agari.mean(), '前方率(%)': rate(group['推定脚質'].isin(['先頭（4角）', '先行寄り']).sum(), group['推定脚質'].ne('不明').sum()), '差し追込率(%)': rate(group['推定脚質'].eq('後方寄り').sum(), group['推定脚質'].ne('不明').sum()), 'ペース判定対象走数': int(group['推定ペース'].ne('不明').sum()), '脚質判定対象走数': int(group['推定脚質'].ne('不明').sum())}


