from test_report import TestReportGenerator
from typing import Dict, List, Any
from datetime import datetime
import os


class ReportExporter:
    """报告导出器"""
    
    def __init__(self, report_generator: TestReportGenerator = None):
        self.report_generator = report_generator if report_generator else TestReportGenerator()
        self.export_dir = "exports"
        
        # 确保导出目录存在
        if not os.path.exists(self.export_dir):
            os.makedirs(self.export_dir)
    
    def export_to_html(self, data: Dict[str, Any], filename: str = None) -> str:
        """导出报告为HTML格式
        
        Args:
            data: 报告数据
            filename: 文件名，不包含扩展名
        
        Returns:
            导出文件的完整路径
        """
        if not filename:
            filename = f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        filepath = os.path.join(self.export_dir, f"{filename}.html")
        
        html_content = self._generate_html_report(data)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        return filepath
    
    def export_to_excel(self, data: Dict[str, Any], filename: str = None) -> str:
        """导出报告为Excel格式
        
        Args:
            data: 报告数据
            filename: 文件名，不包含扩展名
        
        Returns:
            导出文件的完整路径
        """
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        except ImportError:
            raise ImportError("请安装 openpyxl 库: pip install openpyxl")
        
        if not filename:
            filename = f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        filepath = os.path.join(self.export_dir, f"{filename}.xlsx")
        
        # 创建Excel工作簿
        wb = Workbook()
        ws = wb.active
        ws.title = "测试报告"
        
        # 定义样式
        header_font = Font(name='微软雅黑', size=12, bold=True, color='FFFFFF')
        header_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
        header_alignment = Alignment(horizontal='center', vertical='center')
        cell_alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
        thin_border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # 写入标题
        ws.merge_cells('A1:E1')
        title_cell = ws['A1']
        title_cell.value = f"测试报告 - {datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}"
        title_cell.font = Font(name='微软雅黑', size=16, bold=True)
        title_cell.alignment = header_alignment
        
        # 写入概览信息
        overview = data.get('overview', {})
        row = 3
        
        ws[f'A{row}'] = '统计概览'
        ws[f'A{row}'].font = header_font
        ws[f'A{row}'].fill = header_fill
        ws[f'A{row}'].alignment = header_alignment
        ws[f'A{row}'].border = thin_border
        
        ws[f'B{row}'] = '总执行次数'
        ws[f'B{row}'].font = header_font
        ws[f'B{row}'].fill = header_fill
        ws[f'B{row}'].alignment = header_alignment
        ws[f'B{row}'].border = thin_border
        
        ws[f'C{row}'] = overview.get('total_runs', 0)
        ws[f'C{row}'].alignment = cell_alignment
        ws[f'C{row}'].border = thin_border
        
        row += 1
        ws[f'A{row}'] = ''
        ws[f'A{row}'].border = thin_border
        ws[f'B{row}'] = '通过次数'
        ws[f'B{row}'].font = header_font
        ws[f'B{row}'].fill = header_fill
        ws[f'B{row}'].alignment = header_alignment
        ws[f'B{row}'].border = thin_border
        
        ws[f'C{row}'] = overview.get('passed_runs', 0)
        ws[f'C{row}'].alignment = cell_alignment
        ws[f'C{row}'].border = thin_border
        
        row += 1
        ws[f'A{row}'] = ''
        ws[f'A{row}'].border = thin_border
        ws[f'B{row}'] = '失败次数'
        ws[f'B{row}'].font = header_font
        ws[f'B{row}'].fill = header_fill
        ws[f'B{row}'].alignment = header_alignment
        ws[f'B{row}'].border = thin_border
        
        ws[f'C{row}'] = overview.get('failed_runs', 0)
        ws[f'C{row}'].alignment = cell_alignment
        ws[f'C{row}'].border = thin_border
        
        row += 1
        ws[f'A{row}'] = ''
        ws[f'A{row}'].border = thin_border
        ws[f'B{row}'] = '通过率'
        ws[f'B{row}'].font = header_font
        ws[f'B{row}'].fill = header_fill
        ws[f'B{row}'].alignment = header_alignment
        ws[f'B{row}'].border = thin_border
        
        ws[f'C{row}'] = f"{overview.get('pass_rate', 0)}%"
        ws[f'C{row}'].alignment = cell_alignment
        ws[f'C{row}'].border = thin_border
        
        row += 1
        ws[f'A{row}'] = ''
        ws[f'A{row}'].border = thin_border
        ws[f'B{row}'] = '平均执行时间'
        ws[f'B{row}'].font = header_font
        ws[f'B{row}'].fill = header_fill
        ws[f'B{row}'].alignment = header_alignment
        ws[f'B{row}'].border = thin_border
        
        ws[f'C{row}'] = f"{overview.get('avg_duration', 0)}秒"
        ws[f'C{row}'].alignment = cell_alignment
        ws[f'C{row}'].border = thin_border
        
        # 写入用例统计
        row += 2
        case_stats = data.get('case_statistics', [])
        
        ws[f'A{row}'] = '用例统计'
        ws[f'A{row}'].font = header_font
        ws[f'A{row}'].fill = header_fill
        ws[f'A{row}'].alignment = header_alignment
        ws[f'A{row}'].border = thin_border
        
        ws[f'B{row}'] = '用例名称'
        ws[f'B{row}'].font = header_font
        ws[f'B{row}'].fill = header_fill
        ws[f'B{row}'].alignment = header_alignment
        ws[f'B{row}'].border = thin_border
        
        ws[f'C{row}'] = '总执行次数'
        ws[f'C{row}'].font = header_font
        ws[f'C{row}'].fill = header_fill
        ws[f'C{row}'].alignment = header_alignment
        ws[f'C{row}'].border = thin_border
        
        ws[f'D{row}'] = '通过次数'
        ws[f'D{row}'].font = header_font
        ws[f'D{row}'].fill = header_fill
        ws[f'D{row}'].alignment = header_alignment
        ws[f'D{row}'].border = thin_border
        
        ws[f'E{row}'] = '失败次数'
        ws[f'E{row}'].font = header_font
        ws[f'E{row}'].fill = header_fill
        ws[f'E{row}'].alignment = header_alignment
        ws[f'E{row}'].border = thin_border
        
        row += 1
        for case in case_stats:
            ws[f'A{row}'] = ''
            ws[f'A{row}'].border = thin_border
            ws[f'B{row}'] = case.get('case_name', '')
            ws[f'B{row}'].alignment = cell_alignment
            ws[f'B{row}'].border = thin_border
            ws[f'C{row}'] = case.get('total_runs', 0)
            ws[f'C{row}'].alignment = cell_alignment
            ws[f'C{row}'].border = thin_border
            ws[f'D{row}'] = case.get('passed_runs', 0)
            ws[f'D{row}'].alignment = cell_alignment
            ws[f'D{row}'].border = thin_border
            ws[f'E{row}'] = case.get('failed_runs', 0)
            ws[f'E{row}'].alignment = cell_alignment
            ws[f'E{row}'].border = thin_border
            row += 1
        
        # 调整列宽
        ws.column_dimensions['A'].width = 5
        ws.column_dimensions['B'].width = 30
        ws.column_dimensions['C'].width = 15
        ws.column_dimensions['D'].width = 15
        ws.column_dimensions['E'].width = 15
        
        # 保存文件
        wb.save(filepath)
        
        return filepath
    
    def export_to_pdf(self, data: Dict[str, Any], filename: str = None) -> str:
        """导出报告为PDF格式
        
        Args:
            data: 报告数据
            filename: 文件名，不包含扩展名
        
        Returns:
            导出文件的完整路径
        """
        if not filename:
            filename = "test_report_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        
        filepath = os.path.join(self.export_dir, filename + ".pdf")
        
        # 尝试使用reportlab库生成PDF
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas
            from reportlab.lib.units import cm
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            
            # 尝试注册中文字体
            try:
                # 尝试使用Windows系统自带的SimHei字体
                simhei_path = "C:\\Windows\\Fonts\\simhei.ttf"
                if os.path.exists(simhei_path):
                    pdfmetrics.registerFont(TTFont('SimHei', simhei_path))
                    print("成功注册SimHei字体")
                else:
                    print("SimHei字体文件不存在，将使用默认字体")
            except Exception as e:
                print("注册中文字体失败:", str(e))
            
            # 创建PDF画布
            c = canvas.Canvas(filepath, pagesize=A4)
            width, height = A4
            
            # 设置字体
            try:
                # 尝试使用注册的SimHei字体
                c.setFont('SimHei', 16)
            except:
                # 如果没有SimHei字体，使用默认字体
                c.setFont('Helvetica-Bold', 16)
            
            # 添加标题
            c.drawCentredString(width/2, height - 2*cm, '测试报告')
            
            # 设置字体大小
            try:
                c.setFont('SimHei', 10)
            except:
                c.setFont('Helvetica', 10)
            
            # 添加生成时间
            c.drawCentredString(width/2, height - 3*cm, '生成时间: ' + datetime.now().strftime('%Y年%m月%d日 %H:%M:%S'))
            
            # 定义起始位置
            y = height - 5*cm
            line_height = 0.8*cm
            bottom_margin = 2*cm
            
            # 检查是否需要分页的辅助函数
            def check_page_break(needed_lines=1):
                nonlocal y
                if y - needed_lines * line_height < bottom_margin:
                    c.showPage()  # 新建一页
                    try:
                        c.setFont('SimHei', 10)
                    except:
                        c.setFont('Helvetica', 10)
                    y = height - 2*cm  # 新页的起始位置
                    return True
                return False
            
            # 添加统计概览
            try:
                c.setFont('SimHei', 12)
            except:
                c.setFont('Helvetica-Bold', 12)
            c.drawString(2*cm, y, '统计概览')
            y -= line_height
            
            try:
                c.setFont('SimHei', 10)
            except:
                c.setFont('Helvetica', 10)
            overview = data.get('overview', {})
            check_page_break(5)  # 统计概览需要5行
            c.drawString(3*cm, y, '总执行次数: ' + str(overview.get('total_runs', 0)))
            y -= line_height
            c.drawString(3*cm, y, '通过次数: ' + str(overview.get('passed_runs', 0)))
            y -= line_height
            c.drawString(3*cm, y, '失败次数: ' + str(overview.get('failed_runs', 0)))
            y -= line_height
            c.drawString(3*cm, y, '通过率: ' + str(round(overview.get('pass_rate', 0), 2)) + '%')
            y -= line_height
            c.drawString(3*cm, y, '平均执行时间: ' + str(round(overview.get('avg_duration', 0), 2)) + '秒')
            y -= 2*line_height
            
            # 添加状态分布
            try:
                c.setFont('SimHei', 12)
            except:
                c.setFont('Helvetica-Bold', 12)
            c.drawString(2*cm, y, '状态分布')
            y -= line_height
            
            try:
                c.setFont('SimHei', 10)
            except:
                c.setFont('Helvetica', 10)
            status_dist = data.get('status_distribution', [])
            for item in status_dist:
                check_page_break(1)
                status = item.get('status', '未知')
                count = item.get('count', 0)
                percentage = item.get('percentage', 0)
                c.drawString(3*cm, y, status + ': ' + str(count) + ' (' + str(round(percentage, 2)) + '%)')
                y -= line_height
            y -= 2*line_height
            
            # 添加耗时分布
            try:
                c.setFont('SimHei', 12)
            except:
                c.setFont('Helvetica-Bold', 12)
            c.drawString(2*cm, y, '耗时分布')
            y -= line_height
            
            try:
                c.setFont('SimHei', 10)
            except:
                c.setFont('Helvetica', 10)
            duration_dist_dict = data.get('duration_distribution', {})
            duration_dist = duration_dist_dict.get('distribution', []) if isinstance(duration_dist_dict, dict) else []
            for item in duration_dist:
                check_page_break(1)
                duration_range = item.get('range', '未知')
                count = item.get('count', 0)
                percentage = item.get('percentage', 0)
                c.drawString(3*cm, y, duration_range + ': ' + str(count) + ' (' + str(round(percentage, 2)) + '%)')
                y -= line_height
            y -= 2*line_height
            
            # 添加用例统计
            try:
                c.setFont('SimHei', 12)
            except:
                c.setFont('Helvetica-Bold', 12)
            c.drawString(2*cm, y, '用例统计')
            y -= line_height
            
            try:
                c.setFont('SimHei', 10)
            except:
                c.setFont('Helvetica', 10)
            case_stats = data.get('case_statistics', [])
            for item in case_stats:
                check_page_break(6)  # 每个用例需要6行
                # 优先使用case_name键，兼容name键
                case_name = item.get('case_name', item.get('name', '未知'))
                total_runs = item.get('total_runs', 0)
                passed_runs = item.get('passed_runs', 0)
                failed_runs = item.get('failed_runs', 0)
                pass_rate = item.get('pass_rate', 0)
                avg_duration = item.get('avg_duration', 0)
                c.drawString(3*cm, y, '用例名称: ' + case_name)
                y -= line_height
                c.drawString(4*cm, y, '总执行次数: ' + str(total_runs))
                y -= line_height
                c.drawString(4*cm, y, '通过次数: ' + str(passed_runs))
                y -= line_height
                c.drawString(4*cm, y, '失败次数: ' + str(failed_runs))
                y -= line_height
                c.drawString(4*cm, y, '通过率: ' + str(round(pass_rate, 2)) + '%')
                y -= line_height
                c.drawString(4*cm, y, '平均耗时: ' + str(round(avg_duration, 2)) + '秒')
                y -= 2*line_height
            y -= 2*line_height
            
            # 添加项目统计
            try:
                c.setFont('SimHei', 12)
            except:
                c.setFont('Helvetica-Bold', 12)
            c.drawString(2*cm, y, '项目统计')
            y -= line_height
            
            try:
                c.setFont('SimHei', 10)
            except:
                c.setFont('Helvetica', 10)
            project_stats = data.get('project_statistics', [])
            for item in project_stats:
                check_page_break(7)  # 每个项目需要7行
                # 优先使用project_name键，兼容name键
                project_name = item.get('project_name', item.get('name', '未知'))
                total_cases = item.get('total_cases', 0)
                total_runs = item.get('total_runs', 0)
                passed_runs = item.get('passed_runs', 0)
                failed_runs = item.get('failed_runs', 0)
                pass_rate = item.get('pass_rate', 0)
                avg_duration = item.get('avg_duration', 0)
                c.drawString(3*cm, y, '项目名称: ' + project_name)
                y -= line_height
                c.drawString(4*cm, y, '用例数: ' + str(total_cases))
                y -= line_height
                c.drawString(4*cm, y, '总执行次数: ' + str(total_runs))
                y -= line_height
                c.drawString(4*cm, y, '通过次数: ' + str(passed_runs))
                y -= line_height
                c.drawString(4*cm, y, '失败次数: ' + str(failed_runs))
                y -= line_height
                c.drawString(4*cm, y, '通过率: ' + str(round(pass_rate, 2)) + '%')
                y -= line_height
                c.drawString(4*cm, y, '平均耗时: ' + str(round(avg_duration, 2)) + '秒')
                y -= 2*line_height
            
            # 保存PDF
            c.save()
            print("使用reportlab库生成PDF成功")
            return filepath
        except Exception as e:
            print("reportlab库使用失败:", str(e))
            # 如果reportlab失败，尝试使用xhtml2pdf
            pass
        
        # 生成HTML内容
        html_content = self._generate_simplified_html_report(data)
        
        # 尝试使用xhtml2pdf生成PDF
        try:
            from xhtml2pdf import pisa
            
            # 将HTML内容写入PDF文件
            with open(filepath, "wb") as pdf_file:
                pisa_status = pisa.CreatePDF(
                    html_content,
                    dest=pdf_file
                )
            
            if pisa_status.err:
                raise Exception("xhtml2pdf生成PDF失败: " + str(pisa_status.err))
            
            print("使用xhtml2pdf库生成PDF成功")
            return filepath
        except Exception as e:
            print("xhtml2pdf库使用失败:", str(e))
            raise Exception("PDF导出失败: " + str(e))
    
    def _generate_simplified_html_report(self, data: Dict[str, Any]) -> str:
        """生成简化版HTML报告内容，避免触发OCR
        
        Args:
            data: 报告数据
        
        Returns:
            简化版HTML字符串
        """
        overview = data.get('overview', {})
        status_dist = data.get('status_distribution', [])
        duration_dist_dict = data.get('duration_distribution', {})
        duration_dist = duration_dist_dict.get('distribution', []) if isinstance(duration_dist_dict, dict) else []
        case_stats = data.get('case_statistics', [])
        project_stats = data.get('project_statistics', [])
        
        # 使用更简单的HTML和CSS，确保xhtml2pdf能够正确渲染
        html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>测试报告</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
            padding: 0;
        }
        .container {
            width: 100%;
            max-width: 1000px;
            margin: 0 auto;
        }
        h1 {
            color: #1890ff;
            text-align: center;
            margin-bottom: 30px;
        }
        .section {
            margin-bottom: 30px;
        }
        .section-title {
            font-size: 18px;
            font-weight: bold;
            color: #333;
            margin-bottom: 15px;
            padding-bottom: 5px;
            border-bottom: 2px solid #1890ff;
        }
        .overview-grid {
            display: table;
            width: 100%;
            margin-bottom: 30px;
        }
        .overview-row {
            display: table-row;
        }
        .overview-card {
            display: table-cell;
            padding: 15px;
            text-align: center;
            border: 1px solid #e8e8e8;
            margin: 5px;
        }
        .overview-card .label {
            font-size: 14px;
            color: #666;
            margin-bottom: 5px;
        }
        .overview-card .value {
            font-size: 24px;
            font-weight: bold;
            color: #333;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
        }
        th, td {
            padding: 10px;
            text-align: left;
            border: 1px solid #e8e8e8;
        }
        th {
            background-color: #f0f2f5;
            font-weight: bold;
            color: #333;
        }
        .footer {
            text-align: center;
            color: #999;
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #e8e8e8;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>测试报告</h1>
        <p style="text-align: center; color: #999; margin-bottom: 30px;">
            生成时间: ''' + datetime.now().strftime('%Y年%m月%d日 %H:%M:%S') + '''
        </p>
        
        <div class="section">
            <div class="section-title">统计概览</div>
            <div class="overview-grid">
                <div class="overview-row">
                    <div class="overview-card">
                        <div class="label">总执行次数</div>
                        <div class="value">''' + str(overview.get('total_runs', 0)) + '''</div>
                    </div>
                    <div class="overview-card">
                        <div class="label">通过次数</div>
                        <div class="value">''' + str(overview.get('passed_runs', 0)) + '''</div>
                    </div>
                    <div class="overview-card">
                        <div class="label">失败次数</div>
                        <div class="value">''' + str(overview.get('failed_runs', 0)) + '''</div>
                    </div>
                    <div class="overview-card">
                        <div class="label">通过率</div>
                        <div class="value">''' + str(round(overview.get('pass_rate', 0), 2)) + '%' + '''</div>
                    </div>
                    <div class="overview-card">
                        <div class="label">平均执行时间</div>
                        <div class="value">''' + str(round(overview.get('avg_duration', 0), 2)) + '秒' + '''</div>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="section">
            <div class="section-title">状态分布</div>
            <table>
                <thead>
                    <tr>
                        <th>状态</th>
                        <th>次数</th>
                        <th>占比</th>
                    </tr>
                </thead>
                <tbody>
                    ''' + ''.join(["<tr><td>" + str(item.get('status', '未知')) + "</td><td>" + str(item.get('count', 0)) + "</td><td>" + str(round(item.get('percentage', 0), 2)) + "%</td></tr>" for item in status_dist]) + '''
                </tbody>
            </table>
        </div>
        
        <div class="section">
            <div class="section-title">耗时分布</div>
            <table>
                <thead>
                    <tr>
                        <th>耗时区间</th>
                        <th>用例数</th>
                        <th>占比</th>
                    </tr>
                </thead>
                <tbody>
                    ''' + ''.join(["<tr><td>" + str(item.get('range', '未知')) + "</td><td>" + str(item.get('count', 0)) + "</td><td>" + str(round(item.get('percentage', 0), 2)) + "%</td></tr>" for item in duration_dist]) + '''
                </tbody>
            </table>
        </div>
        
        <div class="section">
            <div class="section-title">用例统计</div>
            <table>
                <thead>
                    <tr>
                        <th>用例名称</th>
                        <th>总执行次数</th>
                        <th>通过次数</th>
                        <th>失败次数</th>
                        <th>通过率</th>
                        <th>平均耗时</th>
                    </tr>
                </thead>
                <tbody>
                    ''' + ''.join(["<tr><td>" + str(item.get('name', '未知')) + "</td><td>" + str(item.get('total_runs', 0)) + "</td><td>" + str(item.get('passed_runs', 0)) + "</td><td>" + str(item.get('failed_runs', 0)) + "</td><td>" + str(round(item.get('pass_rate', 0), 2)) + "%</td><td>" + str(round(item.get('avg_duration', 0), 2)) + "秒</td></tr>" for item in case_stats]) + '''
                </tbody>
            </table>
        </div>
        
        <div class="section">
            <div class="section-title">项目统计</div>
            <table>
                <thead>
                    <tr>
                        <th>项目名称</th>
                        <th>用例数</th>
                        <th>总执行次数</th>
                        <th>通过次数</th>
                        <th>失败次数</th>
                        <th>通过率</th>
                        <th>平均耗时</th>
                    </tr>
                </thead>
                <tbody>
                    ''' + ''.join(["<tr><td>" + str(item.get('project_name', '未知')) + "</td><td>" + str(item.get('total_cases', 0)) + "</td><td>" + str(item.get('total_runs', 0)) + "</td><td>" + str(item.get('passed_runs', 0)) + "</td><td>" + str(item.get('failed_runs', 0)) + "</td><td>" + str(round(item.get('pass_rate', 0), 2)) + "%</td><td>" + str(round(item.get('avg_duration', 0), 2)) + "秒</td></tr>" for item in project_stats]) + '''
                </tbody>
            </table>
        </div>
        
        <div class="footer">
            <p>本报告由UI自动化测试平台自动生成</p>
        </div>
    </div>
</body>
</html>
'''
        
        return html
    
    def _generate_html_report(self, data: Dict[str, Any]) -> str:
        """生成HTML报告内容
        
        Args:
            data: 报告数据
        
        Returns:
            HTML字符串
        """
        overview = data.get('overview', {})
        status_dist = data.get('status_distribution', [])
        duration_dist_dict = data.get('duration_distribution', {})
        duration_dist = duration_dist_dict.get('distribution', []) if isinstance(duration_dist_dict, dict) else []
        case_stats = data.get('case_statistics', [])
        project_stats = data.get('project_statistics', [])
        
        # 使用三重引号和转义来避免f-string解析问题
        html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>测试报告</title>
    <style>
        body {
            font-family: 'Microsoft YaHei', Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background-color: white;
            padding: 30px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
        }
        h1 {
            color: #1890ff;
            text-align: center;
            margin-bottom: 30px;
        }
        .section {
            margin-bottom: 30px;
        }
        .section-title {
            font-size: 18px;
            font-weight: bold;
            color: #333;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 2px solid #1890ff;
        }
        .overview-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        .overview-card {
            background-color: #f8f9fa;
            padding: 20px;
            border-radius: 8px;
            text-align: center;
        }
        .overview-card .label {
            color: #666;
            font-size: 14px;
            margin-bottom: 10px;
        }
        .overview-card .value {
            font-size: 28px;
            font-weight: bold;
            color: #1890ff;
        }
        .overview-card.passed .value {
            color: #52c41a;
        }
        .overview-card.failed .value {
            color: #ff4d4f;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 20px;
        }
        th, td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #e8e8e8;
        }
        th {
            background-color: #fafafa;
            font-weight: bold;
            color: #333;
        }
        tr:hover {
            background-color: #f5f5f5;
        }
        .status-badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: bold;
        }
        .status-passed {
            background-color: #f6ffed;
            color: #52c41a;
            border: 1px solid #b7eb8f;
        }
        .status-failed {
            background-color: #fff1f0;
            color: #ff4d4f;
            border: 1px solid #ffa39e;
        }
        .footer {
            text-align: center;
            color: #999;
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #e8e8e8;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>测试报告</h1>
        <p style="text-align: center; color: #999; margin-bottom: 30px;">
            生成时间: ''' + datetime.now().strftime('%Y年%m月%d日 %H:%M:%S') + '''
        </p>
        
        <div class="section">
            <div class="section-title">统计概览</div>
            <div class="overview-grid">
                <div class="overview-card">
                    <div class="label">总执行次数</div>
                    <div class="value">''' + str(overview.get('total_runs', 0)) + '''</div>
                </div>
                <div class="overview-card passed">
                    <div class="label">通过次数</div>
                    <div class="value">''' + str(overview.get('passed_runs', 0)) + '''</div>
                </div>
                <div class="overview-card failed">
                    <div class="label">失败次数</div>
                    <div class="value">''' + str(overview.get('failed_runs', 0)) + '''</div>
                </div>
                <div class="overview-card">
                    <div class="label">通过率</div>
                    <div class="value">''' + str(overview.get('pass_rate', 0)) + '''%</div>
                </div>
                <div class="overview-card">
                    <div class="label">平均执行时间</div>
                    <div class="value">''' + str(overview.get('avg_duration', 0)) + '''秒</div>
                </div>
            </div>
        </div>
        
        <div class="section">
            <div class="section-title">状态分布</div>
            <table>
                <thead>
                    <tr>
                        <th>状态</th>
                        <th>数量</th>
                        <th>占比</th>
                    </tr>
                </thead>
                <tbody>
                    ''' + self._generate_status_rows(status_dist, overview.get('total_runs', 1)) + '''
                </tbody>
            </table>
        </div>
        
        <div class="section">
            <div class="section-title">耗时分布</div>
            <table>
                <thead>
                    <tr>
                        <th>耗时区间</th>
                        <th>数量</th>
                        <th>占比</th>
                    </tr>
                </thead>
                <tbody>
                    ''' + self._generate_duration_rows(duration_dist, overview.get('total_runs', 1)) + '''
                </tbody>
            </table>
        </div>
        
        <div class="section">
            <div class="section-title">用例统计</div>
            <table>
                <thead>
                    <tr>
                        <th>用例名称</th>
                        <th>总执行次数</th>
                        <th>通过次数</th>
                        <th>失败次数</th>
                        <th>通过率</th>
                        <th>平均耗时</th>
                    </tr>
                </thead>
                <tbody>
                    ''' + self._generate_case_rows(case_stats) + '''
                </tbody>
            </table>
        </div>
        
        <div class="section">
            <div class="section-title">项目统计</div>
            <table>
                <thead>
                    <tr>
                        <th>项目名称</th>
                        <th>用例数量</th>
                        <th>总执行次数</th>
                        <th>通过次数</th>
                        <th>失败次数</th>
                        <th>通过率</th>
                        <th>平均耗时</th>
                    </tr>
                </thead>
                <tbody>
                    ''' + self._generate_project_rows(project_stats) + '''
                </tbody>
            </table>
        </div>
        
        <div class="footer">
            <p>本报告由UI自动化测试平台自动生成</p>
        </div>
    </div>
</body>
</html>'''
        
        return html
    
    def _generate_status_rows(self, status_dist: List[Dict], total: int) -> str:
        """生成状态表格行"""
        rows = []
        for item in status_dist:
            status = item.get('status', 'unknown')
            count = item.get('count', 0)
            percentage = (count / total * 100) if total > 0 else 0
            
            status_class = 'status-passed' if status == 'passed' else 'status-failed' if status == 'failed' else ''
            
            rows.append(f"""
                    <tr>
                        <td><span class="status-badge {status_class}">{status}</span></td>
                        <td>{count}</td>
                        <td>{percentage:.2f}%</td>
                    </tr>""")
        
        return '\n'.join(rows)
    
    def _generate_duration_rows(self, duration_dist: List[Dict], total: int) -> str:
        """生成耗时表格行"""
        rows = []
        for item in duration_dist:
            range_label = item.get('range', '')
            count = item.get('count', 0)
            percentage = (count / total * 100) if total > 0 else 0
            
            rows.append(f"""
                    <tr>
                        <td>{range_label}</td>
                        <td>{count}</td>
                        <td>{percentage:.2f}%</td>
                    </tr>""")
        
        return '\n'.join(rows)
    
    def _generate_case_rows(self, case_stats: List[Dict]) -> str:
        """生成用例表格行"""
        rows = []
        for case in case_stats:
            rows.append(f"""
                    <tr>
                        <td>{case.get('case_name', '')}</td>
                        <td>{case.get('total_runs', 0)}</td>
                        <td>{case.get('passed_runs', 0)}</td>
                        <td>{case.get('failed_runs', 0)}</td>
                        <td>{case.get('pass_rate', 0)}%</td>
                        <td>{case.get('avg_duration', 0)}秒</td>
                    </tr>""")
        
        return '\n'.join(rows)
    
    def _generate_project_rows(self, project_stats: List[Dict]) -> str:
        """生成项目表格行"""
        rows = []
        for project in project_stats:
            rows.append(f"""
                    <tr>
                        <td>{project.get('project_name', '')}</td>
                        <td>{project.get('total_cases', 0)}</td>
                        <td>{project.get('total_runs', 0)}</td>
                        <td>{project.get('passed_runs', 0)}</td>
                        <td>{project.get('failed_runs', 0)}</td>
                        <td>{project.get('pass_rate', 0)}%</td>
                        <td>{project.get('avg_duration', 0)}秒</td>
                    </tr>""")
        
        return '\n'.join(rows)
