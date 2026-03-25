#!/usr/bin/env python3
"""
License 生成工具 - 用于生成企业版或试用版授权
"""
import argparse
import sys
from license_manager import LicenseManager, LicenseType


def main():
    parser = argparse.ArgumentParser(description='UAT Platform License 生成工具')
    parser.add_argument('type', choices=['enterprise', 'trial', 'personal'],
                        help='License 类型')
    parser.add_argument('--to', '-t', required=True,
                        help='授权对象（公司名或个人名）')
    parser.add_argument('--days', '-d', type=int, default=365,
                        help='有效期天数（默认: 365）')
    parser.add_argument('--users', '-u', type=int, default=None,
                        help='最大用户数（默认: 企业版无限制，试用版5个）')
    parser.add_argument('--projects', '-p', type=int, default=None,
                        help='最大项目数（默认: 企业版无限制，试用版5个）')
    parser.add_argument('--cases', '-c', type=int, default=None,
                        help='每项目最大用例数（默认: 企业版无限制，试用版100个）')
    parser.add_argument('--executions', '-e', type=int, default=None,
                        help='每月最大执行次数（默认: 企业版无限制，试用版500次）')
    parser.add_argument('--output', '-o', default='license.key',
                        help='输出文件名（默认: license.key）')

    args = parser.parse_args()

    # 确定 License 类型
    license_type_map = {
        'enterprise': LicenseType.ENTERPRISE,
        'trial': LicenseType.TRIAL,
        'personal': LicenseType.PERSONAL
    }
    license_type = license_type_map[args.type]

    # 构建自定义限制
    custom_limits = {}
    if args.users is not None:
        custom_limits['max_users'] = args.users
    if args.projects is not None:
        custom_limits['max_projects'] = args.projects
    if args.cases is not None:
        custom_limits['max_cases_per_project'] = args.cases
    if args.executions is not None:
        custom_limits['max_executions_per_month'] = args.executions

    # 生成 License
    lm = LicenseManager()
    license_str = lm.generate_license(
        license_type=license_type,
        issued_to=args.to,
        expires_days=args.days,
        custom_limits=custom_limits if custom_limits else None
    )

    # 保存到文件
    with open(args.output, 'w') as f:
        f.write(license_str)

    # 验证并显示信息
    result = lm.validate_license(license_str)

    print(f"\n{'='*60}")
    print(f"License 生成成功！")
    print(f"{'='*60}")
    print(f"类型: {args.type.upper()}")
    print(f"授权对象: {args.to}")
    print(f"有效期: {args.days} 天")
    print(f"输出文件: {args.output}")
    print(f"\n验证信息:")
    print(f"  - 状态: {'有效' if result['valid'] else '无效'}")
    print(f"  - 消息: {result['message']}")

    if result['info']:
        info = result['info']
        print(f"\n限制详情:")
        print(f"  - 最大用户数: {'无限制' if info.max_users == -1 else info.max_users}")
        print(f"  - 最大项目数: {'无限制' if info.max_projects == -1 else info.max_projects}")
        print(f"  - 每项目用例数: {'无限制' if info.max_cases_per_project == -1 else info.max_cases_per_project}")
        print(f"  - 每月执行次数: {'无限制' if info.max_executions_per_month == -1 else info.max_executions_per_month}")
        print(f"\n可用功能:")
        for feature in info.features:
            print(f"  - {feature}")

    print(f"\n{'='*60}")
    print(f"请将 {args.output} 文件复制到应用根目录")
    print(f"{'='*60}\n")


if __name__ == '__main__':
    main()
