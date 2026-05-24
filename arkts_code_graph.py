#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ArkTS → SQLite 代码知识图谱
=============================
将鸿蒙 ArkTS 项目自动转换为单文件 SQLite 数据库，
使用硅基流动免费大模型实现自然语言查询代码。

依赖安装：
    pip install openai

使用方式：
    1. 修改下方 PROJECT_PATH 为你的鸿蒙项目路径
    2. python arkts_code_graph.py
    3. 自动生成 code_graph.db，进入交互式查询

作者：SOLO 自动生成
"""

import os
import re
import sqlite3
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# ============================================================
# 配置区 - 只需修改这里
# ============================================================

# 你的鸿蒙项目根目录（绝对路径或相对路径）
PROJECT_PATH = "/你的/鸿蒙/项目/路径"

# 硅基流动 API 配置（从环境变量读取，安全）
SILICONFLOW_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
SILICONFLOW_BASE_URL = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
SILICONFLOW_MODEL = os.environ.get("SILICONFLOW_MODEL", "Qwen/Qwen2.5-7B-Instruct")  # 免费模型

# 输出数据库文件名
DB_FILE = "code_graph.db"

# 要扫描的文件扩展名
ARKTS_EXTENSIONS = {".ets", ".ts"}

# ============================================================
# 数据模型
# ============================================================

@dataclass
class ArkTSMethod:
    """方法信息"""
    name: str
    return_type: str = "void"
    params: list = field(default_factory=list)
    called_methods: list = field(default_factory=list)  # [(method_name, class_name), ...]
    nested_components: list = field(default_factory=list)  # [component_name, ...]
    decorators: list = field(default_factory=list)


@dataclass
class ArkTSClass:
    """类/组件信息"""
    name: str
    file_path: str
    is_component: bool = False
    is_entry: bool = False
    decorators: list = field(default_factory=list)
    methods: list = field(default_factory=list)
    parent_class: Optional[str] = None


@dataclass
class ArkTSImport:
    """导入信息"""
    symbols: str  # 导入的符号（逗号分隔）或 *
    source: str   # 来源模块
    is_type: bool = False  # 是否是 type import


@dataclass
class ArkTSFile:
    """文件信息"""
    path: str
    line_count: int = 0
    classes: list = field(default_factory=list)
    imports: list = field(default_factory=list)


# ============================================================
# ArkTS 解析器（纯 Python 实现）
# ============================================================

class ArkTSParser:
    """
    纯 Python 实现的 ArkTS 源码解析器。
    通过正则表达式和语法模式匹配，提取类、方法、调用关系、组件嵌套等信息。
    """

    def __init__(self):
        # 非代码范围缓存：content_hash -> ranges
        self._non_code_cache = {}

    # 匹配装饰器: @Component, @Entry, @State, @Prop 等
    RE_DECORATOR = re.compile(r'@(\w+)(?:\s*\(([^)]*)\))?')

    # 匹配类声明: class ClassName extends BaseClass implements IFace
    RE_CLASS = re.compile(
        r'\bclass\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+([\w,\s]+))?'
    )

    # 匹配 ArkTS struct 声明: @Component struct StructName { ... }
    RE_STRUCT = re.compile(
        r'\bstruct\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+([\w,\s]+))?'
    )

    # 匹配方法声明: methodName(params): ReturnType
    RE_METHOD = re.compile(
        r'(?:async\s+)?(\w+)\s*\(([^)]*)\)\s*(?::\s*([\w<>\[\],\s]+))?'
    )

    # 匹配方法调用: this.methodName(), ClassName.methodName(), methodName()
    # 支持: 链式调用 .method(), 可选链 ?.method(), 闭包链 ).method()
    RE_METHOD_CALL = re.compile(
        r'(?:this\.|(\w+)\.)(\w+)\s*\('
    )
    # 额外匹配链式调用: ).method(), ].method(), ?.method(), word?.method()
    RE_CHAIN_CALL = re.compile(
        r'(?:(?:\)|\])\s*\??\.|(\w+)\s*\?\.)\s*(\w+)\s*\('
    )

    # 匹配组件使用: Component(), <Component>, Component({ ... })
    RE_COMPONENT_USAGE = re.compile(
        r'(?:^|[\s(<,;])([A-Z]\w+)\s*(?:\(|{|>)'
    )

    # 匹配 import 语句（支持命名、默认、混合、type导入）
    RE_IMPORT = re.compile(
        r'import\s+(?:type\s+)?(?:\{([^}]+)\}|(\w+))(?:\s*,\s*(?:\{([^}]+)\}|(\w+)))?\s+from\s+[\'"]([^\'"]+)[\'"]'
    )

    # 匹配 aboutToAppear / aboutToDisappear / build 等生命周期方法
    LIFECYCLE_METHODS = {
        'build', 'aboutToAppear', 'aboutToDisappear',
        'onPageShow', 'onPageHide', 'onBackPress',
        'onReady', 'onShow', 'onHide', 'onDestroy',
        'onForeground', 'onBackground', 'onCreate', 'onWindowStageCreate',
    }

    # 常见 UI 组件名（大写开头）
    COMMON_COMPONENTS = {
        'Column', 'Row', 'Stack', 'List', 'Grid', 'Scroll', 'Tabs', 'TabContent',
        'Swiper', 'Text', 'Button', 'Image', 'TextInput', 'TextArea', 'Toggle',
        'Slider', 'Select', 'Dialog', 'Alert', 'ActionSheet', 'LoadingProgress',
        'Progress', 'Gauge', 'QRCode', 'Marquee', 'RichText', 'Span',
        'Container', 'Panel', 'Popup', 'Navigation', 'NavDestination',
        'WebView', 'Video', 'XComponent', 'Canvas', 'If', 'ForEach',
        'LazyForEach', 'ListItem', 'GridItem', 'TabBarItem', 'Gesture',
        'ListItemGroup', 'Search', 'Blank', 'Divider', 'Flex',
    }

    def parse_project(self, project_path: str) -> list:
        """
        解析整个项目，返回 ArkTSFile 列表。
        """
        project_path = Path(project_path)
        if not project_path.exists():
            raise FileNotFoundError(f"项目路径不存在: {project_path}")

        files = []
        arkts_files = []
        # 递归查找所有 .ets 和 .ts 文件
        for ext in ARKTS_EXTENSIONS:
            arkts_files.extend(project_path.rglob(f"*{ext}"))

        # 排除 node_modules, oh_modules, .ohos, build 等目录
        exclude_dirs = {'node_modules', 'oh_modules', '.ohos', 'build',
                        '.preview', 'dist', 'out', '.git', 'libs'}

        for file_path in arkts_files:
            # 跳过排除目录
            if any(part in exclude_dirs for part in file_path.parts):
                continue

            try:
                arkts_file = self.parse_file(str(file_path))
                if arkts_file:
                    files.append(arkts_file)
            except Exception as e:
                print(f"  ⚠️ 解析文件失败 {file_path}: {e}")

        return files

    def parse_file(self, file_path: str) -> Optional[ArkTSFile]:
        """解析单个 ArkTS 文件"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except (FileNotFoundError, IOError, PermissionError):
            return None

        if not content.strip():
            return None

        lines = content.split('\n')
        arkts_file = ArkTSFile(path=file_path, line_count=len(lines))

        # 提取所有类
        classes = self._extract_classes(content, file_path)
        arkts_file.classes = classes

        # 提取所有导入
        imports = self._extract_imports(content)
        arkts_file.imports = imports

        return arkts_file

    def _extract_imports(self, content: str) -> list:
        """从文件内容中提取所有import语句"""
        imports = []

        # 匹配各种import模式
        # 1. import { a, b } from 'module'
        # 2. import type { a } from 'module'
        # 3. import * as name from 'module'
        # 4. import defaultExport from 'module'
        # 5. import { a } from 'module' assert { type: 'json' }

        import_patterns = [
            # import type { symbols } from 'source'
            (r'import\s+type\s+\{([^}]+)\}\s+from\s+[\'"]([^\'"]+)[\'"]', True),
            # import { symbols } from 'source'
            (r'import\s+\{([^}]+)\}\s+from\s+[\'"]([^\'"]+)[\'"]', False),
            # import * as name from 'source'
            (r'import\s+\*\s+as\s+(\w+)\s+from\s+[\'"]([^\'"]+)[\'"]', False),
            # import defaultExport from 'source'
            (r'import\s+(\w+)\s+from\s+[\'"]([^\'"]+)[\'"]', False),
        ]

        for pattern, is_type in import_patterns:
            for match in re.finditer(pattern, content):
                symbols = match.group(1).strip()
                source = match.group(2).strip()
                imports.append(ArkTSImport(symbols=symbols, source=source, is_type=is_type))

        return imports

    def _extract_classes(self, content: str, file_path: str) -> list:
        """从文件内容中提取所有类定义（支持 class 和 struct）"""
        classes = []

        # 预计算哪些位置在字符串或注释中
        non_code_ranges = self._find_non_code_ranges(content)

        # 找到所有类/struct 定义的位置
        class_matches = list(self.RE_CLASS.finditer(content))
        struct_matches = list(self.RE_STRUCT.finditer(content))

        # 合并并按位置排序
        all_matches = [(m, 'class') for m in class_matches] + [(m, 'struct') for m in struct_matches]
        all_matches.sort(key=lambda x: x[0].start())

        for i, (match, decl_type) in enumerate(all_matches):
            # 跳过在字符串或注释中的类定义
            if self._is_in_range(match.start(), non_code_ranges):
                continue

            class_name = match.group(1)
            parent_class = match.group(2)
            decorators = []

            # 向上查找装饰器
            start_pos = match.start()
            preceding = content[:start_pos]
            lines_before = preceding.split('\n')
            for line in reversed(lines_before):
                stripped = line.strip()
                if not stripped or stripped.startswith('//') or stripped.startswith('/*') or stripped.startswith('*'):
                    continue
                if stripped.startswith('@'):
                    dec_matches = self.RE_DECORATOR.findall(stripped)
                    # 检查装饰器后是否还有属性声明（非类声明）
                    # 如果有，停止向上查找（避免把属性装饰器归到类上）
                    last_dec_end = 0
                    for dm in self.RE_DECORATOR.finditer(stripped):
                        last_dec_end = dm.end()
                    after_dec = stripped[last_dec_end:].strip()
                    if after_dec and not after_dec.startswith(('class', 'struct', 'export', 'default')):
                        # 装饰器后是属性声明等，停止（不添加此装饰器）
                        break
                    # 装饰器后无其他内容或以关键字开头，是类装饰器
                    for dec_name, dec_args in dec_matches:
                        decorators.append(dec_name)
                elif stripped.startswith('export') or stripped.startswith('default'):
                    # 跳过 export/default 关键字行，继续向上查找装饰器
                    continue
                else:
                    break

            is_component = 'Component' in decorators
            is_entry = 'Entry' in decorators
            # ArkTS struct 默认视为组件（如果有 @Component 装饰器则确认）
            if decl_type == 'struct' and not decorators:
                is_component = False  # 无装饰器的 struct 不算组件

            # 提取类体
            class_start = match.end()
            # 需要跳过泛型参数中的 { }（如 <T extends { id: string }>）
            # 找到类声明后真正的类体 {
            real_class_start = class_start
            depth = 0
            i = class_start
            while i < len(content):
                ch = content[i]
                if ch == '<':
                    depth += 1
                elif ch == '>':
                    depth -= 1
                elif ch == '{' and depth == 0:
                    real_class_start = i
                    break
                elif ch == '{' and depth > 0:
                    pass  # 泛型约束中的 {，跳过
                i += 1

            # 找到类体的结束位置（简单的大括号匹配）
            class_body = self._extract_block(content, real_class_start)

            # 找出类体中嵌套的类/struct范围，用于排除嵌套类的方法
            nested_class_ranges = []
            # 对 class_body 重新计算非代码范围（因为位置是相对于 class_body 的）
            body_non_code_ranges = self._find_non_code_ranges(class_body)
            for nested_match in self.RE_CLASS.finditer(class_body):
                if not self._is_in_range(nested_match.start(), body_non_code_ranges):
                    # 找到嵌套类的类体范围
                    nc_start = nested_match.start()
                    # 找到嵌套类的 { 开始位置
                    nc_brace_pos = class_body.find('{', nested_match.end())
                    if nc_brace_pos >= 0:
                        nc_body = self._extract_block(class_body, nc_brace_pos)
                        # 范围从 struct/class 声明开始到类体闭合 } 结束
                        nested_class_ranges.append((nc_start, nc_brace_pos + len(nc_body)))
            for nested_match in self.RE_STRUCT.finditer(class_body):
                if not self._is_in_range(nested_match.start(), body_non_code_ranges):
                    nc_start = nested_match.start()
                    nc_brace_pos = class_body.find('{', nested_match.end())
                    if nc_brace_pos >= 0:
                        nc_body = self._extract_block(class_body, nc_brace_pos)
                        nested_class_ranges.append((nc_start, nc_brace_pos + len(nc_body)))

            # 提取方法（传入嵌套类范围以排除）
            methods = self._extract_methods(class_body, class_name, nested_class_ranges)

            cls = ArkTSClass(
                name=class_name,
                file_path=file_path,
                is_component=is_component,
                is_entry=is_entry,
                decorators=decorators,
                methods=methods,
                parent_class=parent_class,
            )
            classes.append(cls)

        return classes

    def _find_non_code_ranges(self, content: str) -> list:
        """找到所有字符串和注释的范围（用于过滤假匹配，带缓存）"""
        # 使用内容哈希作为缓存键
        content_hash = hash(content)
        if content_hash in self._non_code_cache:
            return self._non_code_cache[content_hash]
        
        ranges = []
        i = 0
        n = len(content)

        while i < n:
            ch = content[i]

            # 单行注释
            if ch == '/' and i + 1 < n and content[i + 1] == '/':
                start = i
                while i < n and content[i] != '\n':
                    i += 1
                ranges.append((start, i))
                continue

            # 多行注释
            if ch == '/' and i + 1 < n and content[i + 1] == '*':
                start = i
                i += 2
                while i < n:
                    if content[i] == '*' and i + 1 < n and content[i + 1] == '/':
                        i += 2
                        break
                    i += 1
                ranges.append((start, i))
                continue

            # 字符串（单引号、双引号）
            if ch in ('"', "'"):
                string_char = ch
                start = i
                i += 1
                while i < n:
                    if content[i] == '\\':
                        i += 2
                        continue
                    if content[i] == string_char:
                        i += 1
                        break
                    i += 1
                ranges.append((start, i))
                continue

            # 模板字符串（反引号），${...} 中的代码不算非代码
            if ch == '`':
                start = i
                i += 1
                while i < n:
                    if content[i] == '\\':
                        i += 2
                        continue
                    if content[i] == '`':
                        i += 1
                        break
                    # ${...} 插值中的代码是可执行代码
                    if content[i] == '$' and i + 1 < n and content[i + 1] == '{':
                        # 先记录模板字符串文字部分
                        ranges.append((start, i))
                        i += 2  # 跳过 ${
                        # 跳过插值内容（括号平衡）
                        brace_depth = 1
                        while i < n and brace_depth > 0:
                            if content[i] == '{':
                                brace_depth += 1
                            elif content[i] == '}':
                                brace_depth -= 1
                            elif content[i] in ('"', "'", '`'):
                                # 跳过插值中的嵌套字符串
                                inner_char = content[i]
                                i += 1
                                while i < n:
                                    if content[i] == '\\':
                                        i += 2
                                        continue
                                    if content[i] == inner_char:
                                        break
                                    i += 1
                            i += 1
                        # 插值结束，继续模板字符串
                        start = i  # 下一部分模板字符串的起始位置
                        continue
                    i += 1
                # 记录最后的模板字符串部分
                if start < i:
                    ranges.append((start, i))
                continue

            i += 1

        # 存入缓存
        self._non_code_cache[content_hash] = ranges
        return ranges

    def _is_in_range(self, pos: int, ranges: list) -> bool:
        """检查位置是否在某个范围内（使用二分搜索优化）"""
        # ranges 已按起始位置排序，使用二分搜索 O(log n)
        if not ranges:
            return False
        
        # 二分搜索：找到可能包含 pos 的范围
        left, right = 0, len(ranges) - 1
        while left <= right:
            mid = (left + right) // 2
            start, end = ranges[mid]
            if start <= pos < end:
                return True
            elif pos < start:
                right = mid - 1
            else:  # pos >= end
                left = mid + 1
        return False

    def _extract_block(self, content: str, start: int) -> str:
        """从 start 位置提取一个 {} 块的内容（包含闭合的}）"""
        depth = 0
        i = start
        in_string = False
        string_char = None
        in_line_comment = False
        in_block_comment = False
        in_template_expr = False  # 模板字符串 ${...} 插值
        template_expr_depth = 0  # 插值中的独立括号深度
        in_regex = False  # 正则字面量

        while i < len(content):
            ch = content[i]

            # 处理注释
            if in_line_comment:
                if ch == '\n':
                    in_line_comment = False
                i += 1
                continue
            if in_block_comment:
                if ch == '*' and i + 1 < len(content) and content[i + 1] == '/':
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue
            
            # 处理正则字面量
            if in_regex:
                if ch == '\\':
                    i += 2  # 跳过转义字符
                    continue
                if ch == '/':
                    # 正则结束，跳过flags
                    i += 1
                    while i < len(content) and content[i] in 'gimsuy':
                        i += 1
                    in_regex = False
                    continue
                if ch == '\n':
                    # 正则不能跨行，可能是误判
                    in_regex = False
                i += 1
                continue
            
            if not in_string and not in_template_expr:
                if ch == '/' and i + 1 < len(content):
                    if content[i + 1] == '/':
                        in_line_comment = True
                        i += 2
                        continue
                    if content[i + 1] == '*':
                        in_block_comment = True
                        i += 2
                        continue
                    # 检查是否是正则字面量（/ 后面不是 / 或 *）
                    # 正则字面量的条件：前面是运算符、括号、关键字等
                    # 简化判断：前面不是 ) ] } 字母 数字 _ $ " ' ` 时认为是正则
                    if i > 0:
                        prev = content[i - 1]
                        if prev not in ')]}0123456789"\'`' and not prev.isalnum() and prev != '_':
                            in_regex = True
                            i += 1
                            continue

            # 处理模板字符串插值 ${...}
            if in_template_expr:
                if ch == '{':
                    template_expr_depth += 1
                elif ch == '}':
                    template_expr_depth -= 1
                    if template_expr_depth == 0:
                        in_template_expr = False
                elif ch in ('"', "'", '`'):
                    # 插值中的嵌套字符串
                    inner_char = ch
                    i += 1
                    while i < len(content):
                        if content[i] == '\\':
                            i += 2
                            continue
                        if content[i] == inner_char:
                            break
                        i += 1
                i += 1
                continue

            # 处理字符串
            if in_string:
                if ch == '\\':
                    i += 2
                    continue
                if ch == string_char:
                    if string_char == '`':
                        # 模板字符串结束
                        in_string = False
                    else:
                        in_string = False
                elif string_char == '`' and ch == '$' and i + 1 < len(content) and content[i + 1] == '{':
                    # 进入模板字符串插值
                    in_template_expr = True
                    template_expr_depth = 1  # ${ 中的 { 已经打开了一层
                    i += 2  # 跳过 ${
                    continue
                i += 1
                continue
            else:
                if ch in ('"', "'"):
                    in_string = True
                    string_char = ch
                    i += 1
                    continue
                if ch == '`':
                    in_string = True
                    string_char = ch
                    i += 1
                    continue

            # 处理大括号
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return content[start:i + 1]  # 包含闭合的 }

            i += 1

        return content[start:]  # 未找到匹配，返回剩余内容

    def _extract_methods(self, class_body: str, class_name: str, nested_class_ranges: list = None) -> list:
        """从类体中提取所有方法"""
        methods = []
        if nested_class_ranges is None:
            nested_class_ranges = []
        keywords = {
            'if', 'else', 'for', 'while', 'do', 'switch', 'case', 'break',
            'continue', 'return', 'new', 'typeof', 'instanceof',
            'in', 'of', 'class', 'extends', 'implements', 'import', 'export',
            'from', 'as', 'try', 'catch', 'finally', 'throw', 'async', 'await',
            'function', 'const', 'let', 'var', 'static', 'private', 'public',
            'protected', 'abstract', 'override', 'super', 'this',
            'true', 'false', 'null', 'undefined', 'void', 'type', 'interface',
            'enum', 'namespace', 'module', 'declare', 'require',
            # ArkTS 装饰器关键字（不是方法名）
            'Component', 'Entry', 'State', 'Prop', 'Provide', 'Consume',
            'Builder', 'BuilderParam', 'Watch', 'Link', 'StorageLink',
            'StorageProp', 'Extend', 'Styles', 'Animatable', 'Observed',
            'ObjectLink', 'Track', 'Volatile', 'Reusable', 'LazyForEach',
        }
        arkts_ui_components = {
            'Column', 'Row', 'Stack', 'List', 'Grid', 'Scroll', 'Tabs', 'TabContent',
            'Swiper', 'Text', 'Button', 'Image', 'TextInput', 'TextArea', 'Toggle',
            'Slider', 'Select', 'Dialog', 'Alert', 'ActionSheet', 'LoadingProgress',
            'Progress', 'Gauge', 'QRCode', 'Marquee', 'RichText', 'Span',
            'Container', 'Panel', 'Popup', 'Navigation', 'NavDestination',
            'WebView', 'Video', 'XComponent', 'Canvas', 'If', 'ForEach',
            'LazyForEach', 'ListItem', 'GridItem', 'TabBarItem', 'Gesture',
            'ListItemGroup', 'Search', 'SearchBar', 'Blank', 'Divider', 'Flex',
            'Badge', 'Circle', 'Ellipse', 'Line', 'Path', 'Polygon', 'Polyline',
            'Rect', 'Shape', 'Stepper', 'StepperItem', 'TabsController',
            'TextClock', 'TextPicker', 'TimePicker', 'Web', 'Counter',
            'DataPanel', 'Form', 'GridRow', 'GridCol', 'MenuItem', 'Menu',
            'NavRouter', 'SideBarContainer', 'Radio', 'Rating', 'Checkbox',
        }

        # 预计算非代码区域
        non_code_ranges = self._find_non_code_ranges(class_body)

        # 正则匹配所有方法声明位置
        # 匹配: [修饰符] 方法名[(泛型)](参数)
        # 注意：不在此正则中匹配泛型参数（因为可能嵌套 <>），改为手动跳过
        method_pattern = re.compile(
            r'(?<![.\w])'  # 前面不是 . 字母数字
            r'((?:(?:public|private|protected|static|async|override|abstract)\s+)*)'
            r'(\w+)'
            r'\s*'
        )

        # 匹配箭头函数属性: name = (...) => { ... } 或 name = (...) => expression
        arrow_func_pattern = re.compile(
            r'(?<![.\w])'
            r'((?:(?:public|private|protected|static|async|override|abstract)\s+)*)'
            r'(\w+)'
            r'\s*=\s*\('
        )

        def find_open_paren_after_name(body: str, after_pos: int) -> int:
            """从方法名后查找匹配的 ( 位置，跳过泛型参数 <...>"""
            i = after_pos
            while i < len(body):
                ch = body[i]
                if ch == '<':
                    # 跳过泛型参数（支持嵌套 <>）
                    depth = 1
                    i += 1
                    while i < len(body) and depth > 0:
                        if body[i] == '<':
                            depth += 1
                        elif body[i] == '>':
                            depth -= 1
                        i += 1
                    continue
                if ch == '(':
                    return i
                if ch in ('\n', ';', '{', '}', '='):
                    # 遇到换行、分号、大括号、等号说明不是方法声明
                    return -1
                i += 1
            return -1

        # 合并两种模式的匹配结果，按位置排序
        all_matches = []
        for m in method_pattern.finditer(class_body):
            # 手动查找 ( 位置
            paren_pos = find_open_paren_after_name(class_body, m.end())
            if paren_pos >= 0:
                all_matches.append((m, paren_pos))
        arrow_matches = list(arrow_func_pattern.finditer(class_body))
        # 去重：如果同一位置已被 method_pattern 匹配，跳过箭头匹配
        arrow_positions = set()
        for m, pp in all_matches:
            for i in range(m.start(), m.end()):
                arrow_positions.add(i)
        for am in arrow_matches:
            if am.start() not in arrow_positions:
                all_matches.append((am, am.end() - 1))  # arrow_func的(在end()-1
        # 按位置排序
        all_matches.sort(key=lambda x: x[0].start())
        processed_end = 0  # 已处理到的字符位置

        for match, actual_paren_start in all_matches:
            pos = match.start()

            # 跳过已处理过的区域
            if pos < processed_end:
                continue

            # 跳过非代码区域
            if self._is_in_range(pos, non_code_ranges):
                continue

            # 跳过嵌套类/struct的范围
            if self._is_in_range(pos, nested_class_ranges):
                continue

            modifiers = match.group(1).strip() if match.group(1) else ''
            method_name = match.group(2)

            # 跳过以数字开头的标识符（不是有效方法名）
            if method_name[0].isdigit():
                continue

            # 跳过关键字和 UI 组件
            if method_name in keywords or method_name in arkts_ui_components:
                continue

            # 找到参数列表的右括号（括号平衡）
            paren_start = actual_paren_start  # 使用实际找到的 ( 位置
            paren_depth = 0
            paren_end = paren_start
            for k in range(paren_start, len(class_body)):
                ch = class_body[k]
                if ch == '(':
                    paren_depth += 1
                elif ch == ')':
                    paren_depth -= 1
                    if paren_depth == 0:
                        paren_end = k
                        break
            if paren_depth != 0:
                continue

            params_str = class_body[paren_start + 1:paren_end]

            # 提取返回类型
            rest = class_body[paren_end + 1:].lstrip()
            return_type = 'void'
            if rest.startswith(':'):
                type_start = 1
                angle_depth = 0
                for j in range(type_start, len(rest)):
                    ch = rest[j]
                    if ch == '<':
                        angle_depth += 1
                    elif ch == '>':
                        # 检查是否是 => 箭头（不是泛型关闭）
                        if j > 0 and rest[j - 1] == '=':
                            pass  # => 箭头，不影响 depth
                        elif angle_depth > 0:
                            angle_depth -= 1
                    elif ch == '{' and angle_depth == 0:
                        # 可能是对象字面量返回类型，也可能是方法体开始
                        # 检查后面是否有匹配的 }，且 } 后面还有 {
                        brace_depth = 1
                        k = j + 1
                        found_close = False
                        while k < len(rest) and brace_depth > 0:
                            if rest[k] == '{':
                                brace_depth += 1
                            elif rest[k] == '}':
                                brace_depth -= 1
                                if brace_depth == 0:
                                    found_close = True
                            k += 1
                        if found_close and k < len(rest) and rest[k:].lstrip().startswith('{'):
                            # {...} 是返回类型的一部分，后面还有方法体 {
                            # 跳过这个对象字面量
                            j = k - 1  # for loop 的 j += 1 会跳过 }
                            continue
                        # 否则 { 是方法体开始
                        return_type = rest[type_start:j].strip() or 'void'
                        break
                    elif ch == ';' and angle_depth == 0:
                        return_type = rest[type_start:j].strip() or 'void'
                        break
                    elif ch == '\n' and angle_depth == 0:
                        # 换行时检查后面是否还有泛型参数 <
                        # 如果有，继续扫描（支持跨行的泛型类型如 Promise\n<\nT\n>）
                        remaining_after_nl = rest[j + 1:].lstrip()
                        if remaining_after_nl.startswith('<'):
                            continue  # 继续扫描泛型参数
                        return_type = rest[type_start:j].strip() or 'void'
                        break
                    elif angle_depth == 0 and j > type_start:
                        # 检测下一个方法声明或类体结束
                        # 当遇到空格后的关键字/修饰符或标识符+( 模式时停止
                        remaining = rest[j:].lstrip()
                        if remaining and (remaining[0] == '}'):
                            return_type = rest[type_start:j].strip() or 'void'
                            break
                        # 检测方法声明模式：[修饰符] 名称 (
                        if remaining and re.match(r'(?:(?:public|private|protected|static|async|override|abstract)\s+)?\w+\s*\(', remaining):
                            return_type = rest[type_start:j].strip() or 'void'
                            break
                else:
                    return_type = rest[type_start:].strip() or 'void'

            # 检查是抽象方法（以 ; 结尾）还是普通方法（以 { 开头）
            # 先跳过返回类型（如果有）
            after_paren = class_body[paren_end + 1:].lstrip()
            if after_paren.startswith(':'):
                # 跳过返回类型，找到 { 或 ;
                # 返回类型可能包含泛型如 Map<string, List<number>>
                # 也可能包含函数类型如 (event: string) => void
                # 也可能包含数组类型如 string[]
                # 注意：只跟踪 <> 的深度，() 和 {} 不影响 depth
                # 因为 () 可能属于下一个方法声明，{} 可能是方法体
                angle_depth = 0
                k = paren_end + 1
                while k < len(class_body):
                    ch = class_body[k]
                    if ch == '<':
                        angle_depth += 1
                    elif ch == '>':
                        # 检查是否是 => 箭头（不是泛型关闭）
                        if k > 0 and class_body[k - 1] == '=':
                            # => 箭头，跳过，不影响 depth
                            pass
                        elif angle_depth > 0:
                            angle_depth -= 1
                    elif ch == '{' and angle_depth == 0:
                        # 可能是对象字面量返回类型，检查后面是否有匹配的 }
                        # 且 } 后面还有 {（方法体）
                        brace_depth = 1
                        bk = k + 1
                        found_close = False
                        while bk < len(class_body) and brace_depth > 0:
                            if class_body[bk] == '{':
                                brace_depth += 1
                            elif class_body[bk] == '}':
                                brace_depth -= 1
                                if brace_depth == 0:
                                    found_close = True
                            bk += 1
                        if found_close and bk < len(class_body):
                            # 检查 } 后面是否还有 {（方法体开始）
                            rest_after = class_body[bk:].lstrip()
                            if rest_after.startswith('{'):
                                # 第一个 { 是返回类型中的对象字面量
                                # 继续跳过，找到真正的 { 方法体
                                k = bk
                                continue
                        # 否则 { 是方法体开始
                        after_type = '{'
                        break
                    elif ch == ';' and angle_depth == 0:
                        after_type = ';'
                        break
                    elif ch == '\n' and angle_depth == 0:
                        # 换行时检查后面是否还有泛型参数 <
                        # 如果有，继续扫描（支持跨行的泛型类型）
                        remaining_after_nl = class_body[k + 1:].lstrip()
                        if remaining_after_nl.startswith('<'):
                            k += 1  # 手动增加，因为 continue 会跳过 k += 1
                            continue  # 继续扫描泛型参数
                        # 没有找到 { 或 ;，说明是抽象方法（无分号）或声明不完整
                        after_type = ''
                        break
                    elif angle_depth == 0 and k > paren_end + 1:
                        # 检测下一个方法声明或类体结束
                        remaining = class_body[k:].lstrip()
                        if remaining and remaining[0] == '}':
                            after_type = ''
                            break
                        if remaining and re.match(r'(?:(?:public|private|protected|static|async|override|abstract)\s+)?\w+\s*\(', remaining):
                            after_type = ''
                            break
                    k += 1
                else:
                    after_type = ''  # 未找到
            else:
                # after_paren 不以 : 开头（无返回类型）
                # 检查是否是抽象方法（无返回类型、无方法体）
                after_type = after_paren[0] if after_paren else ''

            is_abstract = after_type == ';'
            if not is_abstract and after_type == '':
                # 无分号无方法体：可能是 abstract 方法、declare class 方法、或接口方法
                if 'abstract' in modifiers:
                    is_abstract = True
                else:
                    # 利用已提取的 return_type 来确定返回类型结束位置
                    rest_after_paren = class_body[paren_end + 1:].lstrip()
                    if rest_after_paren.startswith(':'):
                        # 在 rest_after_paren 中找到 return_type 的实际位置
                        # rest_after_paren 格式: ": ReturnType ..."
                        # 需要跳过 ": " 然后找到 return_type 的结束位置
                        colon_idx = rest_after_paren.find(':')
                        if colon_idx >= 0:
                            after_colon = rest_after_paren[colon_idx + 1:].lstrip()
                            # after_colon 是 "ReturnType ..."（去掉前导空格）
                            # return_type 已经被正确提取，找到它在 after_colon 中的位置
                            ret_start_in_after = after_colon.find(return_type)
                            if ret_start_in_after >= 0:
                                # return_type 在 rest_after_paren 中的结束位置
                                # = colon_idx + 1 + (空格数) + ret_start_in_after + len(return_type)
                                spaces_before = len(rest_after_paren[colon_idx + 1:]) - len(after_colon)
                                ret_end_abs = colon_idx + 1 + spaces_before + ret_start_in_after + len(return_type)
                                after_ret = rest_after_paren[ret_end_abs:].lstrip()
                                if after_ret and (after_ret[0] == '}' or
                                    re.match(r'(?:(?:public|private|protected|static|async|override|abstract)\s+)?\w+\s*\(', after_ret)):
                                    is_abstract = True
            elif not is_abstract and after_type not in ('{', ';', ''):
                # after_type 是其他字符（如下一个方法声明的开头）
                # 检查是否是抽象方法（无返回类型）
                if 'abstract' in modifiers:
                    is_abstract = True

            has_body = after_type == '{'

            # 提取参数列表
            params = self._parse_params(params_str)

            # 提取方法体
            method_body = ''
            if has_body:
                # 找到方法体的 { 位置（使用find避免ValueError）
                brace_pos = class_body.find('{', paren_end)
                if brace_pos >= 0:
                    method_body = self._extract_block(class_body, brace_pos)
                    processed_end = brace_pos + len(method_body)
                else:
                    # 找不到 {，跳过此方法
                    processed_end = paren_end + 1
            elif is_abstract:
                if after_type == ';':
                    semi_pos = class_body.find(';', paren_end)
                    if semi_pos >= 0:
                        processed_end = semi_pos + 1
                    else:
                        processed_end = paren_end + 1
                else:
                    # 无分号的抽象方法，processed_end 设为返回类型结束位置
                    # 找到返回类型后的换行位置
                    rest = class_body[paren_end + 1:].lstrip()
                    if rest.startswith(':'):
                        # 返回类型在第一行内
                        nl_pos = class_body.find('\n', paren_end + 1)
                        processed_end = nl_pos if nl_pos > 0 else paren_end + 1
                    else:
                        processed_end = paren_end + 1
            else:
                continue

            # 提取方法调用
            called_methods = self._extract_method_calls(method_body, class_name)

            # 提取嵌套组件（仅对 build 方法）
            nested_components = []
            if method_name == 'build':
                nested_components = self._extract_nested_components(method_body)

            # 提取方法装饰器
            decorators = []
            preceding = class_body[:pos]
            lines_before = preceding.split('\n')
            for line in reversed(lines_before):
                stripped = line.strip()
                if not stripped or stripped.startswith('//') or stripped.startswith('/*') or stripped.startswith('*'):
                    continue
                # 查找行中的装饰器（可能不以@开头，如 "{ @Builder"）
                at_pos = stripped.find('@')
                if at_pos >= 0:
                    # 检查 @ 前面是否只有空白或 { （类体开始）
                    before_at = stripped[:at_pos].strip()
                    if before_at and before_at != '{':
                        # @ 前面有其他代码（如属性赋值），停止
                        break
                    dec_str = stripped[at_pos:]
                    dec_match = self.RE_DECORATOR.match(dec_str)
                    if dec_match:
                        after_dec = dec_str[dec_match.end():].strip()
                        if not after_dec:
                            decorators.append(dec_match.group(1))
                        elif re.match(r'\w+\s*\(', after_dec) or re.match(r'\w+\s*<', after_dec):
                            decorators.append(dec_match.group(1))
                            break
                        else:
                            break
                elif stripped.startswith('export') or stripped.startswith('default'):
                    continue
                else:
                    # 遇到非空、非注释、非装饰器、非export的行，停止向上查找
                    break

            method = ArkTSMethod(
                name=method_name,
                return_type=return_type,
                params=params,
                called_methods=called_methods,
                nested_components=nested_components,
                decorators=decorators,
            )
            methods.append(method)

        return methods

    def _parse_params(self, params_str: str) -> list:
        """解析参数列表（支持嵌套括号、字符串内逗号）"""
        params = []
        if not params_str.strip():
            return params

        # 按顶层逗号分割（跳过括号内的逗号和字符串内的逗号）
        angle_depth = 0  # <> 的深度
        bracket_depth = 0  # () 的深度
        brace_depth = 0  # {} 的深度
        square_depth = 0  # [] 的深度（元组类型、数组解构）
        in_string = False  # 是否在字符串内
        string_char = None  # 字符串引号字符
        current = ''
        i = 0
        while i < len(params_str):
            ch = params_str[i]

            # 处理字符串（跳过字符串内的逗号）
            if in_string:
                current += ch
                if ch == '\\':
                    # 转义字符，跳过下一个字符
                    i += 1
                    if i < len(params_str):
                        current += params_str[i]
                elif ch == string_char:
                    in_string = False
                i += 1
                continue

            # 检测字符串开始
            if ch in ('"', "'"):
                in_string = True
                string_char = ch
                current += ch
                i += 1
                continue

            if ch == '<':
                angle_depth += 1
                current += ch
            elif ch == '>':
                if angle_depth > 0:
                    angle_depth -= 1
                current += ch
            elif ch == '(':
                bracket_depth += 1
                current += ch
            elif ch == ')':
                if bracket_depth > 0:
                    bracket_depth -= 1
                current += ch
            elif ch == '{':
                brace_depth += 1
                current += ch
            elif ch == '}':
                if brace_depth > 0:
                    brace_depth -= 1
                current += ch
            elif ch == '[':
                square_depth += 1
                current += ch
            elif ch == ']':
                if square_depth > 0:
                    square_depth -= 1
                current += ch
            elif ch == ',' and angle_depth == 0 and bracket_depth == 0 and brace_depth == 0 and square_depth == 0:
                param = current.strip()
                if param:
                    params.append(self._parse_single_param(param))
                current = ''
            else:
                current += ch
            i += 1

        param = current.strip()
        if param:
            params.append(self._parse_single_param(param))

        return params

    def _parse_single_param(self, param_str: str) -> dict:
        """解析单个参数（修复括号深度混用和函数类型=>问题）"""
        # 处理默认值（顶层 = 分割，跳过字符串内）
        angle_depth = 0   # <>
        paren_depth = 0   # ()
        brace_depth = 0   # {}
        square_depth = 0  # []
        in_string = False
        string_char = None
        eq_pos = -1
        i = 0
        while i < len(param_str):
            ch = param_str[i]
            if in_string:
                if ch == '\\':
                    i += 2
                    continue
                if ch == string_char:
                    in_string = False
                i += 1
                continue
            if ch in ('"', "'"):
                in_string = True
                string_char = ch
                i += 1
                continue
            if ch == '<':
                angle_depth += 1
            elif ch == '>':
                # 检查是否是 => 箭头
                if i > 0 and param_str[i-1] == '=':
                    pass  # => 箭头，不影响深度
                elif angle_depth > 0:
                    angle_depth -= 1
            elif ch == '(':
                paren_depth += 1
            elif ch == ')':
                if paren_depth > 0:
                    paren_depth -= 1
            elif ch == '{':
                brace_depth += 1
            elif ch == '}':
                if brace_depth > 0:
                    brace_depth -= 1
            elif ch == '[':
                square_depth += 1
            elif ch == ']':
                if square_depth > 0:
                    square_depth -= 1
            elif ch == '=':
                # 检查是否是 => 箭头
                if i + 1 < len(param_str) and param_str[i+1] == '>':
                    pass  # => 箭头，不是赋值
                elif angle_depth == 0 and paren_depth == 0 and brace_depth == 0 and square_depth == 0:
                    eq_pos = i
                    break
            i += 1

        if eq_pos >= 0:
            name_type = param_str[:eq_pos].strip()
        else:
            name_type = param_str

        # 分离名称和类型（顶层 : 分割，但跳过函数类型的 =>）
        angle_depth = 0
        paren_depth = 0
        brace_depth = 0
        square_depth = 0
        in_string = False
        string_char = None
        colon_pos = -1
        i = 0
        while i < len(name_type):
            ch = name_type[i]
            if in_string:
                if ch == '\\':
                    i += 2
                    continue
                if ch == string_char:
                    in_string = False
                i += 1
                continue
            if ch in ('"', "'"):
                in_string = True
                string_char = ch
                i += 1
                continue
            if ch == '<':
                angle_depth += 1
            elif ch == '>':
                if i > 0 and name_type[i-1] == '=':
                    pass
                elif angle_depth > 0:
                    angle_depth -= 1
            elif ch == '(':
                paren_depth += 1
            elif ch == ')':
                if paren_depth > 0:
                    paren_depth -= 1
            elif ch == '{':
                brace_depth += 1
            elif ch == '}':
                if brace_depth > 0:
                    brace_depth -= 1
            elif ch == '[':
                square_depth += 1
            elif ch == ']':
                if square_depth > 0:
                    square_depth -= 1
            elif ch == ':':
                # 检查是否是 => 箭头的一部分（前面是 =）
                if i > 0 and name_type[i-1] == '=':
                    pass  # => 箭头的一部分，跳过
                elif angle_depth == 0 and paren_depth == 0 and brace_depth == 0 and square_depth == 0:
                    colon_pos = i
                    break
            i += 1

        if colon_pos >= 0:
            name = name_type[:colon_pos].strip()
            param_type = name_type[colon_pos + 1:].strip()
        else:
            name = name_type
            param_type = 'any'

        # 清理参数名：去除 rest 前缀 ... 和可选后缀 ?
        if name.startswith('...'):
            name = name[3:]
        if name.endswith('?'):
            name = name[:-1]

        return {'name': name, 'type': param_type}

    def _extract_method_calls(self, method_body: str, current_class: str) -> list:
        """从方法体中提取所有方法调用"""
        calls = []
        seen = set()

        # 预计算非代码区域（字符串和注释）
        non_code_ranges = self._find_non_code_ranges(method_body)

        # 跳过语言内置方法和常见 API
        builtins = {
            'log', 'warn', 'error', 'debug', 'trace',
            'then', 'catch', 'finally', 'toString', 'valueOf',
            'hasOwnProperty', 'forEach', 'map', 'filter', 'reduce',
            'find', 'findIndex', 'some', 'every', 'includes',
            'indexOf', 'lastIndexOf', 'push', 'pop', 'shift', 'unshift',
            'splice', 'slice', 'concat', 'join', 'reverse', 'sort',
            'flat', 'flatMap', 'fill', 'copyWithin', 'entries', 'keys',
            'values', 'length', 'charAt', 'charCodeAt', 'startsWith',
            'endsWith', 'replace', 'replaceAll', 'search', 'match',
            'matchAll', 'split', 'substring', 'substr', 'trim',
            'trimStart', 'trimEnd', 'padStart', 'padEnd', 'repeat',
            'toLowerCase', 'toUpperCase', 'toFixed', 'toPrecision',
            'parseInt', 'parseFloat', 'isNaN', 'isFinite',
            'bind', 'call', 'apply', 'assign', 'defineProperty',
            'getPrototypeOf', 'setPrototypeOf', 'freeze', 'seal',
            'preventExtensions', 'isFrozen', 'isSealed',
            'stringify', 'abs', 'ceil', 'floor', 'round', 'max',
            'min', 'random', 'sqrt', 'pow', 'sign', 'trunc', 'cbrt',
            'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'atan2',
            'addEventListener', 'removeEventListener', 'dispatchEvent',
            'querySelector', 'querySelectorAll', 'getElementById',
            'getElementsByClassName', 'getElementsByTagName',
            'setAttribute', 'getAttribute', 'removeAttribute',
            'createElement', 'createTextNode', 'appendChild',
            'removeChild', 'insertBefore', 'replaceChild',
            'setTimeout', 'setInterval', 'clearTimeout', 'clearInterval',
            'requestAnimationFrame', 'cancelAnimationFrame',
            'encodeURI', 'decodeURI', 'encodeURIComponent', 'decodeURIComponent',
            'btoa', 'atob',
        }

        # 第一轮：匹配 this.method() 和 ClassName.method()
        for match in self.RE_METHOD_CALL.finditer(method_body):
            if self._is_in_range(match.start(), non_code_ranges):
                continue

            target_class = match.group(1) or current_class
            method_name = match.group(2)

            if method_name in builtins:
                continue

            key = (method_name, target_class)
            if key not in seen:
                seen.add(key)
                calls.append(key)

        # 第二轮：匹配链式调用 ).method() 和可选链 ?.method()
        for match in self.RE_CHAIN_CALL.finditer(method_body):
            if self._is_in_range(match.start(), non_code_ranges):
                continue

            # group(1) 是可选链前面的对象名（如 this.config?.getName() 中的 config）
            # group(2) 是方法名
            opt_target = match.group(1)
            method_name = match.group(2)

            if method_name in builtins:
                continue

            target_class = opt_target or current_class
            key = (method_name, target_class)
            if key not in seen:
                seen.add(key)
                calls.append(key)

        return calls

    def _extract_nested_components(self, build_body: str) -> list:
        """从 build 方法中提取嵌套的自定义组件"""
        components = []
        seen = set()

        # 预计算非代码区域
        non_code_ranges = self._find_non_code_ranges(build_body)

        for match in self.RE_COMPONENT_USAGE.finditer(build_body):
            # 跳过字符串和注释中的组件
            if self._is_in_range(match.start(), non_code_ranges):
                continue

            comp_name = match.group(1)

            # 跳过常见内置组件
            if comp_name in self.COMMON_COMPONENTS:
                continue

            # 跳过 TypeScript/ArkTS 关键字
            ts_keywords = {
                'If', 'ForEach', 'LazyForEach', 'While', 'Switch', 'For',
                'Else', 'ElseIf', 'True', 'False', 'Null', 'Undefined',
                'This', 'Super', 'New', 'Return', 'Type', 'Interface',
                'Enum', 'Class', 'Extends', 'Implements', 'Import',
                'Export', 'Default', 'From', 'As', 'Is', 'KeyOf',
                'Infer', 'Readonly', 'Partial', 'Required', 'Pick',
                'Omit', 'Record', 'Exclude', 'Extract', 'NonNullable',
                'ReturnType', 'InstanceType', 'Parameters', 'ConstructorParameters',
                'Promise', 'Array', 'Object', 'String', 'Number', 'Boolean',
                'Symbol', 'BigInt', 'Function', 'Undefined', 'Null',
                'Void', 'Never', 'Any', 'Unknown', 'Map', 'Set',
                'WeakMap', 'WeakSet', 'Date', 'RegExp', 'Error',
                'Response', 'Request', 'Headers', 'FormData', 'URL',
                'URLSearchParams', 'AbortController', 'AbortSignal',
                'Iterable', 'Iterator', 'AsyncIterable', 'AsyncIterator',
                'Generator', 'AsyncGenerator', 'ArrayBuffer', 'SharedArrayBuffer',
                'DataView', 'Int8Array', 'Uint8Array', 'Uint8ClampedArray',
                'Int16Array', 'Uint16Array', 'Int32Array', 'Uint32Array',
                'Float32Array', 'Float64Array', 'BigInt64Array', 'BigUint64Array',
            }

            if comp_name in ts_keywords:
                continue

            # 跳过 new 实例化（如 new MyService()）
            match_start = match.start()
            before = build_body[:match_start].rstrip()
            if before.endswith('new'):
                continue

            if comp_name not in seen:
                seen.add(comp_name)
                components.append(comp_name)

        return components


# ============================================================
# 知识图谱构建器
# ============================================================

class CodeKnowledgeGraph:
    """代码知识图谱：将解析结果存入 SQLite"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self._init_db()

    def _init_db(self):
        """初始化数据库表结构（使用CREATE TABLE IF NOT EXISTS避免数据丢失）"""
        self.cursor.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                line_count INTEGER
            );

            CREATE TABLE IF NOT EXISTS classes (
                name TEXT,
                file_path TEXT,
                is_component BOOLEAN DEFAULT 0,
                is_entry BOOLEAN DEFAULT 0,
                parent_class TEXT,
                decorators TEXT DEFAULT '',
                PRIMARY KEY (name, file_path)
            );

            CREATE TABLE IF NOT EXISTS methods (
                name TEXT,
                class_name TEXT,
                return_type TEXT DEFAULT 'void',
                params TEXT DEFAULT '',
                decorators TEXT DEFAULT '',
                PRIMARY KEY (name, class_name, params)
            );

            CREATE TABLE IF NOT EXISTS method_calls (
                caller TEXT,
                caller_class TEXT,
                callee TEXT,
                callee_class TEXT,
                PRIMARY KEY (caller, caller_class, callee, callee_class)
            );

            CREATE TABLE IF NOT EXISTS component_nesting (
                parent TEXT,
                child TEXT,
                PRIMARY KEY (parent, child)
            );

            CREATE TABLE IF NOT EXISTS imports (
                file_path TEXT,
                symbols TEXT,
                source TEXT
            );

            -- 创建索引加速查询
            CREATE INDEX idx_classes_component ON classes(is_component);
            CREATE INDEX idx_classes_entry ON classes(is_entry);
            CREATE INDEX idx_methods_class ON methods(class_name);
            CREATE INDEX idx_calls_caller ON method_calls(caller_class);
            CREATE INDEX idx_calls_callee ON method_calls(callee, callee_class);
            CREATE INDEX idx_nesting_parent ON component_nesting(parent);
        """)
        self.conn.commit()

    def build(self, project_path: str, clean: bool = False):
        """解析项目并构建知识图谱
        
        Args:
            project_path: 项目路径
            clean: 是否清理旧数据（默认False，保留历史数据）
        """
        parser = ArkTSParser()
        files = parser.parse_project(project_path)

        # 如果需要清理旧数据，先删除相关表
        if clean:
            self.cursor.execute("DELETE FROM files")
            self.cursor.execute("DELETE FROM classes")
            self.cursor.execute("DELETE FROM methods")
            self.cursor.execute("DELETE FROM method_calls")
            self.cursor.execute("DELETE FROM component_nesting")
            self.cursor.execute("DELETE FROM imports")
            self.conn.commit()

        # 收集当前项目的所有文件路径，用于后续清理
        current_file_paths = set(f.path for f in files)

        # 开启事务
        self.cursor.execute("BEGIN")

        try:
            file_count = 0
            class_count = 0
            method_count = 0
            call_count = 0
            import_count = 0

            for arkts_file in files:
                # 先清理该文件中旧的类数据（支持同一文件内容变更）
                self.cursor.execute("SELECT name FROM classes WHERE file_path = ?", (arkts_file.path,))
                old_class_names = [row[0] for row in self.cursor.fetchall()]
                current_class_names = set(cls.name for cls in arkts_file.classes)
                for old_name in old_class_names:
                    if old_name not in current_class_names:
                        # 删除旧类及其相关数据
                        self.cursor.execute("DELETE FROM methods WHERE class_name = ?", (old_name,))
                        self.cursor.execute("DELETE FROM method_calls WHERE caller_class = ?", (old_name,))
                        self.cursor.execute("DELETE FROM component_nesting WHERE parent = ?", (old_name,))
                        self.cursor.execute("DELETE FROM classes WHERE name = ? AND file_path = ?", (old_name, arkts_file.path))

                # 插入文件（支持重复build）
                self.cursor.execute(
                    "INSERT OR REPLACE INTO files VALUES (?, ?)",
                    (arkts_file.path, arkts_file.line_count)
                )
                file_count += 1

                # 清理并重新插入该文件的导入信息
                self.cursor.execute("DELETE FROM imports WHERE file_path = ?", (arkts_file.path,))
                for imp in arkts_file.imports:
                    self.cursor.execute(
                        "INSERT INTO imports VALUES (?, ?, ?)",
                        (arkts_file.path, imp.symbols, imp.source)
                    )
                    import_count += 1

                for cls in arkts_file.classes:
                    # 插入类
                    self.cursor.execute(
                        "INSERT OR REPLACE INTO classes VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            cls.name,
                            cls.file_path,
                            int(cls.is_component),
                            int(cls.is_entry),
                            cls.parent_class,
                            ','.join(cls.decorators),
                        )
                    )
                    class_count += 1

                    for method in cls.methods:
                        # 插入方法（支持重载：主键包含参数签名）
                        params_str = ';'.join(
                            f"{p['name']}:{p['type']}" for p in method.params
                        )
                        self.cursor.execute(
                            "INSERT OR REPLACE INTO methods VALUES (?, ?, ?, ?, ?)",
                            (
                                method.name,
                                cls.name,
                                method.return_type,
                                params_str,
                                ','.join(method.decorators),
                            )
                        )
                        method_count += 1

                        # 插入方法调用关系
                        for callee_name, callee_class in method.called_methods:
                            self.cursor.execute(
                                "INSERT OR IGNORE INTO method_calls VALUES (?, ?, ?, ?)",
                                (method.name, cls.name, callee_name, callee_class)
                            )
                            call_count += 1

                        # 插入组件嵌套关系
                        if method.name == 'build' and method.nested_components:
                            for child in method.nested_components:
                                self.cursor.execute(
                                    "INSERT OR IGNORE INTO component_nesting VALUES (?, ?)",
                                    (cls.name, child)
                                )

            # 清理已不存在的文件数据
            if current_file_paths:
                # 获取数据库中所有文件路径
                self.cursor.execute("SELECT path FROM files")
                all_db_paths = [row[0] for row in self.cursor.fetchall()]
                
                for db_path in all_db_paths:
                    if db_path not in current_file_paths:
                        # 删除该文件相关的所有数据
                        self.cursor.execute("DELETE FROM files WHERE path = ?", (db_path,))
                        self.cursor.execute("DELETE FROM imports WHERE file_path = ?", (db_path,))
                        # 获取该文件中的类
                        self.cursor.execute("SELECT name FROM classes WHERE file_path = ?", (db_path,))
                        class_names = [row[0] for row in self.cursor.fetchall()]
                        for cls_name in class_names:
                            self.cursor.execute("DELETE FROM methods WHERE class_name = ?", (cls_name,))
                            self.cursor.execute("DELETE FROM method_calls WHERE caller_class = ?", (cls_name,))
                            self.cursor.execute("DELETE FROM component_nesting WHERE parent = ?", (cls_name,))
                        self.cursor.execute("DELETE FROM classes WHERE file_path = ?", (db_path,))

            self.conn.commit()

            print(f"\n📊 知识图谱构建完成！")
            print(f"   📁 文件数: {file_count}")
            print(f"   📦 类/组件数: {class_count}")
            print(f"   🔧 方法数: {method_count}")
            print(f"   🔗 调用关系数: {call_count}")
            print(f"   📥 导入数: {import_count}")
            print(f"   💾 数据库已保存到: {self.db_path}")

        except Exception as e:
            self.conn.rollback()
            print(f"❌ 构建失败，事务已回滚: {e}")
            raise

    def execute_sql(self, sql: str, params: tuple = ()) -> list:
        """执行 SQL 查询并返回结果（仅允许只读操作）"""
        # 安全检查：只允许 SELECT 语句，阻止 DROP/DELETE/UPDATE/INSERT/ALTER 等
        stripped = sql.strip().upper()
        if not stripped.startswith('SELECT') and not stripped.startswith('PRAGMA'):
            return [{"error": "安全限制：仅允许 SELECT 和 PRAGMA 查询语句"}]
        try:
            self.cursor.execute(sql, params)
            columns = [desc[0] for desc in self.cursor.description]
            return [dict(zip(columns, row)) for row in self.cursor.fetchall()]
        except Exception as e:
            return [{"error": str(e)}]

    def get_schema(self) -> str:
        """获取数据库表结构描述"""
        return """
    表结构:
    - files(path, line_count) -- 项目文件
    - classes(name, file_path, is_component, is_entry, parent_class, decorators) -- 类和组件
    - methods(name, class_name, return_type, params, decorators) -- 方法定义
    - method_calls(caller, caller_class, callee, callee_class) -- 方法调用关系
    - component_nesting(parent, child) -- 组件嵌套关系
    - imports(file_path, symbols, source) -- 导入关系
    """

    def get_stats(self) -> dict:
        """获取图谱统计信息"""
        stats = {}
        tables = ['files', 'classes', 'methods', 'method_calls', 'component_nesting']
        for table in tables:
            self.cursor.execute(f"SELECT COUNT(*) as cnt FROM {table}")
            stats[table] = self.cursor.fetchone()['cnt']
        return stats

    def close(self):
        self.conn.close()


# ============================================================
# AI 查询引擎（硅基流动）
# ============================================================

class AIQueryEngine:
    """使用硅基流动免费大模型进行自然语言查询"""

    def __init__(self, api_key: str, base_url: str, model: str):
        try:
            from openai import OpenAI
            self.client = OpenAI(api_key=api_key, base_url=base_url)
            self.model = model
            self.available = True
        except ImportError:
            self.client = None
            self.model = model
            self.available = False
            print("⚠️ 未安装 openai 库，AI 查询功能不可用。")
            print("   请执行: pip install openai")
            print("   您仍可使用 SQL 直接查询。")

    def ask(self, question: str, graph: CodeKnowledgeGraph) -> list:
        """将自然语言问题转换为 SQL 并执行"""
        if not self.available:
            return [{"error": "openai 库未安装，请执行: pip install openai"}]

        schema = graph.get_schema()

        system_prompt = f"""你是一个 SQL 专家。根据用户的问题和以下表结构，生成 SQLite 查询语句。

{schema}

规则:
1. 只返回一条 SQL 语句，不要任何解释
2. 不要使用 LIMIT（除非用户指定）
3. 使用标准的 SQLite 语法
4. 如果问题涉及组件，注意 is_component 字段是布尔值（0或1）
5. 如果问题涉及入口页面，注意 is_entry 字段是布尔值（0或1）
"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                temperature=0.1,
                max_tokens=200,
            )
            sql = response.choices[0].message.content.strip()

            # 清理可能的 markdown 包裹
            sql = sql.strip('```sql').strip('```').strip()

            print(f"  🔍 生成 SQL: {sql}")
            return graph.execute_sql(sql)

        except Exception as e:
            return [{"error": f"AI 查询失败: {e}"}]


# ============================================================
# 交互式查询界面
# ============================================================

def interactive_mode(graph: CodeKnowledgeGraph, ai_engine: AIQueryEngine):
    """交互式查询模式"""
    print("\n" + "=" * 60)
    print("  🧠 ArkTS 代码知识图谱 - 交互式查询")
    print("=" * 60)
    print(f"  📊 数据库: {graph.db_path}")
    stats = graph.get_stats()
    print(f"  📁 文件: {stats['files']}  📦 类: {stats['classes']}  "
          f"🔧 方法: {stats['methods']}  🔗 调用: {stats['method_calls']}")
    print("=" * 60)
    print("  命令:")
    print("    自然语言提问 → AI 自动生成 SQL 查询")
    print("    SQL: <语句>  → 直接执行 SQL")
    print("    stats       → 查看统计信息")
    print("    schema      → 查看表结构")
    print("    examples    → 查看示例查询")
    print("    quit        → 退出")
    print("=" * 60)

    while True:
        try:
            user_input = input("\n❓ 请输入查询: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见！")
            break

        if not user_input:
            continue

        if user_input.lower() in ('quit', 'exit', 'q'):
            print("👋 再见！")
            break

        if user_input.lower() == 'stats':
            stats = graph.get_stats()
            for table, count in stats.items():
                print(f"  {table}: {count}")
            continue

        if user_input.lower() == 'schema':
            print(graph.get_schema())
            continue

        if user_input.lower() == 'examples':
            print("""
  📝 示例查询:
    列出所有入口页面组件
    哪些类是自定义组件？
    UserService 类在哪个文件？
    哪些方法调用了 getUserInfo？
    UserPage 组件包含哪些子组件？
    列出代码行数最多的前10个文件
    有多少个类继承了基类？
    SQL: SELECT * FROM classes WHERE is_entry = 1
            """)
            continue

        # 如果以 SQL: 开头，直接执行 SQL
        if user_input.upper().startswith('SQL:'):
            sql = user_input[4:].strip()
            print(f"  🔍 执行 SQL: {sql}")
            results = graph.execute_sql(sql)
        else:
            # AI 自然语言查询
            print("  🤖 AI 思考中...")
            results = ai_engine.ask(user_input, graph)

        # 显示结果
        if not results:
            print("  📭 无结果")
        elif 'error' in results[0]:
            print(f"  ❌ {results[0]['error']}")
        else:
            # 表格化输出
            columns = list(results[0].keys())
            col_widths = {}
            for col in columns:
                max_len = len(str(col))
                for row in results:
                    max_len = max(max_len, len(str(row.get(col, ''))))
                col_widths[col] = min(max_len, 50)

            # 表头
            header = " | ".join(
                str(col).ljust(col_widths[col]) for col in columns
            )
            separator = "-+-".join(
                "-" * col_widths[col] for col in columns
            )
            print(f"\n  {header}")
            print(f"  {separator}")

            # 数据行（最多显示 50 行）
            for row in results[:50]:
                line = " | ".join(
                    str(row.get(col, ''))[:col_widths[col]].ljust(col_widths[col])
                    for col in columns
                )
                print(f"  {line}")

            if len(results) > 50:
                print(f"  ... 还有 {len(results) - 50} 行未显示")


# ============================================================
# 主程序
# ============================================================

def main():
    print("=" * 60)
    print("  🔍 ArkTS → SQLite 代码知识图谱")
    print("  🤖 AI 查询: 硅基流动免费版")
    print("=" * 60)

    # 检查项目路径
    if PROJECT_PATH == "/你的/鸿蒙/项目/路径":
        print("\n❌ 请先修改脚本顶部的 PROJECT_PATH 变量为你的鸿蒙项目路径！")
        print("   例如: PROJECT_PATH = '/home/user/my-harmony-app'")
        print("\n💡 提示: 您也可以通过命令行参数指定项目路径:")
        print("   python arkts_code_graph.py /你的/项目/路径")
        sys.exit(1)

    if not os.path.exists(PROJECT_PATH):
        print(f"\n❌ 项目路径不存在: {PROJECT_PATH}")
        sys.exit(1)

    # 构建知识图谱
    print(f"\n📂 项目路径: {PROJECT_PATH}")
    print("🔍 正在解析 ArkTS 项目...")

    graph = CodeKnowledgeGraph(DB_FILE)
    try:
        graph.build(PROJECT_PATH)
    except Exception as e:
        print(f"\n❌ 解析失败: {e}")
        graph.close()
        sys.exit(1)

    # 初始化 AI 查询引擎
    ai_engine = AIQueryEngine(
        api_key=SILICONFLOW_API_KEY,
        base_url=SILICONFLOW_BASE_URL,
        model=SILICONFLOW_MODEL,
    )

    # 示例查询
    if stats := graph.get_stats():
        if stats['classes'] > 0:
            print("\n" + "=" * 60)
            print("  📋 快速预览")
            print("=" * 60)

            # 入口页面
            entries = graph.execute_sql(
                "SELECT name, file_path FROM classes WHERE is_entry = 1 LIMIT 10"
            )
            if entries:
                print(f"\n  🚪 入口页面 ({len(entries)}):")
                for e in entries:
                    print(f"     - {e['name']} ({e['file_path']})")

            # 自定义组件
            components = graph.execute_sql(
                "SELECT name, file_path FROM classes WHERE is_component = 1 LIMIT 10"
            )
            if components:
                print(f"\n  🧩 自定义组件 (前{len(components)}个):")
                for c in components:
                    print(f"     - {c['name']} ({c['file_path']})")

    # 进入交互模式
    interactive_mode(graph, ai_engine)

    graph.close()


if __name__ == "__main__":
    # 支持命令行参数指定项目路径
    if len(sys.argv) > 1:
        PROJECT_PATH = sys.argv[1]

    main()
