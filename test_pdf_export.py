import sys
import os

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from report_exporter import ReportExporter

# 测试数据
test_data = {
    "overview": {
        "total_runs": 10,
        "passed_runs": 8,
        "failed_runs": 2,
        "pass_rate": 80.0,
        "avg_duration": 1.5
    },
    "status_distribution": [
        {"status": "passed", "count": 8, "percentage": 80.0},
        {"status": "failed", "count": 2, "percentage": 20.0}
    ],
    "duration_distribution": {
        "distribution": [
            {"range": "0-1s", "count": 5, "percentage": 50.0},
            {"range": "1-2s", "count": 3, "percentage": 30.0},
            {"range": "2s+", "count": 2, "percentage": 20.0}
        ]
    },
    "case_statistics": [
        {
            "name": "测试用例1",
            "total_runs": 5,
            "passed_runs": 4,
            "failed_runs": 1,
            "pass_rate": 80.0,
            "avg_duration": 1.2
        },
        {
            "name": "测试用例2",
            "total_runs": 5,
            "passed_runs": 4,
            "failed_runs": 1,
            "pass_rate": 80.0,
            "avg_duration": 1.8
        }
    ],
    "project_statistics": [
        {
            "project_name": "测试项目",
            "total_cases": 2,
            "total_runs": 10,
            "passed_runs": 8,
            "failed_runs": 2,
            "pass_rate": 80.0,
            "avg_duration": 1.5
        }
    ]
}

# 创建ReportExporter实例
exporter = ReportExporter()

# 尝试导出PDF
print("开始测试PDF导出...")
try:
    pdf_path = exporter.export_to_pdf(test_data, "test_pdf_export")
    print(f"PDF导出成功！文件路径: {pdf_path}")
except Exception as e:
    print(f"PDF导出失败: {str(e)}")

# 尝试导出HTML
print("\n开始测试HTML导出...")
try:
    html_path = exporter.export_to_html(test_data, "test_html_export")
    print(f"HTML导出成功！文件路径: {html_path}")
except Exception as e:
    print(f"HTML导出失败: {str(e)}")
