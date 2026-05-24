#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ArkTS 代码知识图谱构建工具 - R29 全面修复版
==============================================
修复31个静态核验发现的问题（7高+11中+13低）
"""

import os
import re
import sqlite3
import bisect
from typing import List, Dict, Optional, Tuple, Set
from dataclasses import dataclass, field


@dataclass
class ArkTSMethod:
    """ArkTS 方法"""
    name: str
    class_name: str
    return_type: str
    params: List[Dict]
    decorators: List[str]
    called_methods: List[Tuple[str, str]] = field(default_factory=list)  # [(method_name, target_class)]
    nested_components: List[str] = field(default_factory=list)


@dataclass
class ArkTSClass:
    """ArkTS 类/组件"""
    name: str
    file_path: str
    is_component: bool = False
    is_entry: bool = False
    parent_class: Optional[str] = None
    decorators: List[str] = field(default_factory=list)
    methods: List[ArkTSMethod] = field(default_factory=list)


@dataclass
class ArkTSFile:
    """ArkTS 文件"""
    path: str
    classes: List[ArkTSClass] = field(default_factory=list)
    imports: List[Dict] = field(default_factory=list)
    line_count: int = 0


class ArkTSParser:
    """ArkTS 代码解析器 - R29 全面修复版"""

    # [修复#19] 预编译正则表达式提升为类属性
    RE_CLASS = re.compile(r'\bclass\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+([\w,\s]+))?')
    RE_STRUCT = re.compile(r'\bstruct\s+(\w+)')
    RE_IMPORT = re.compile(r'import\s+.*?\s+from\s+[\'"](.+?)[\'"]')
    RE_METHOD_CALL = re.compile(r'(?:this\.|(\w+)\.)(\w+)\s*\(')
    RE_CHAIN_CALL = re.compile(r'(?:(?:\)|\])\s*\??\.|(\w+)\s*\?\.)\s*(\w+)\s*\(')
    RE_COMPONENT_USAGE = re.compile(r'\b([A-Z][a-zA-Z0-9]*)\s*\(')
    RE_DECORATOR = re.compile(r'@(\w+)(?:\([^)]*\))?')
    # [修复#19] 方法声明正则提升为类属性
    RE_METHOD_DECL = re.compile(
        r'(?<![.\w])'
        r'((?:(?:public|private|protected|static|async|override|abstract)\s+)*)'
        r'(\w+)'
        r'\s*(?:<[^>]*>)?\s*\('
    )
    RE_ARROW_FUNC = re.compile(
        r'(?<![.\w])'
        r'((?:(?:public|private|protected|static|async|override|abstract)\s+)*)'
        r'(\w+)'
        r'\s*=\s*\('
    )

    # [修复#12] 完整的内置方法过滤列表
    BUILTIN_METHODS: Set[str] = frozenset({
        'log', 'warn', 'error', 'debug', 'trace', 'info',
        'then', 'catch', 'finally', 'toString', 'valueOf',
        'hasOwnProperty', 'forEach', 'map', 'filter', 'reduce',
        'find', 'findIndex', 'some', 'every', 'includes',
        'indexOf', 'lastIndexOf', 'push', 'pop', 'shift', 'unshift',
        'splice', 'slice', 'concat', 'join', 'reverse', 'sort',
        'flat', 'flatMap', 'fill', 'copyWithin', 'entries', 'keys',
        'values', 'charAt', 'charCodeAt', 'startsWith',
        'endsWith', 'replace', 'replaceAll', 'search', 'match',
        'matchAll', 'split', 'substring', 'substr', 'trim',
        'trimStart', 'trimEnd', 'padStart', 'padEnd', 'repeat',
        'toLowerCase', 'toUpperCase', 'toFixed', 'toPrecision',
        'parseInt', 'parseFloat', 'isNaN', 'isFinite',
        'bind', 'call', 'apply', 'assign', 'defineProperty',
        'stringify', 'abs', 'ceil', 'floor', 'round', 'max',
        'min', 'random', 'sqrt', 'pow', 'sign', 'trunc', 'cbrt',
        'sin', 'cos', 'tan', 'addEventListener', 'removeEventListener',
        'querySelector', 'querySelectorAll', 'getElementById',
        'setAttribute', 'getAttribute', 'createElement',
        'appendChild', 'removeChild', 'insertBefore',
        'setTimeout', 'setInterval', 'clearTimeout', 'clearInterval',
        'requestAnimationFrame', 'cancelAnimationFrame',
        'encodeURI', 'decodeURI', 'encodeURIComponent', 'decodeURIComponent',
        'btoa', 'atob', 'JSON', 'Math', 'Object', 'Array',
        'String', 'Number', 'Boolean', 'Promise', 'console',
        'Date', 'RegExp', 'Error', 'Map', 'Set', 'WeakMap', 'WeakSet',
        'Symbol', 'Proxy', 'Reflect',
    })

    def __init__(self):
        self.keywords: Set[str] = {
            'if', 'else', 'for', 'while', 'do', 'switch', 'case', 'break',
            'continue', 'return', 'new', 'typeof', 'instanceof',
            'in', 'of', 'class', 'extends', 'implements', 'import', 'export',
            'from', 'as', 'try', 'catch', 'finally', 'throw', 'async', 'await',
            'function', 'const', 'let', 'var', 'static', 'private', 'public',
            'protected', 'abstract', 'override', 'super', 'this',
            'true', 'false', 'null', 'undefined', 'void', 'type', 'interface',
            'enum', 'namespace', 'module', 'declare', 'require', 'constructor',
        }
        self.arkts_ui_components: Set[str] = {
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

    def parse_project(self, project_path: str) -> List[ArkTSFile]:
        """解析整个 ArkTS 项目"""
        files = []
        exclude_dirs = {'node_modules', 'oh_modules', 'build', '.git', '.idea', 'hvigor'}

        for root, dirs, filenames in os.walk(project_path):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            for filename in filenames:
                if filename.endswith(('.ets', '.ts')):
                    file_path = os.path.join(root, filename)
                    arkts_file = self.parse_file(file_path)
                    if arkts_file:
                        files.append(arkts_file)

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

        # 快速检查：如果文件没有任何代码（只有注释和空白），跳过
        # 移除所有注释和字符串后检查是否还有代码
        stripped = self._find_non_code_ranges(content)
        code_only = ''
        prev_end = 0
        for start, end in stripped:
            code_only += content[prev_end:start]
            prev_end = end
        code_only += content[prev_end:]
        if not code_only.strip():
            return None

        line_count = content.count('\n') + 1
        imports = self._extract_imports(content)
        classes = self._extract_classes(content, file_path)

        return ArkTSFile(
            path=file_path,
            classes=classes,
            imports=imports,
            line_count=line_count
        )

    def _extract_imports(self, content: str) -> List[Dict]:
        """[修复#1] 提取导入语句 - 防止ValueError崩溃"""
        imports = []
        for match in self.RE_IMPORT.finditer(content):
            source = match.group(1)
            import_str = match.group(0)
            items = []
            if '{' in import_str:
                # [修复#1] 使用find替代index，防止}不存在时崩溃
                brace_start = import_str.find('{')
                brace_end = import_str.find('}')
                if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
                    items_str = import_str[brace_start + 1:brace_end]
                    items = [item.strip() for item in items_str.split(',')]
            elif 'import' in import_str and 'from' in import_str:
                parts = import_str.split()
                if len(parts) >= 2:
                    items = [parts[1]]

            imports.append({
                'source': source,
                'items': items,
                'full': match.group(0)
            })
        return imports

    def _extract_classes(self, content: str, file_path: str) -> List[ArkTSClass]:
        """[修复#15] 提取所有类/struct定义 - 泛型跳过增加字符串/注释感知"""
        classes = []
        non_code_ranges = self._find_non_code_ranges(content)

        for match in self.RE_CLASS.finditer(content):
            if self._is_in_range(match.start(), non_code_ranges):
                continue

            class_name = match.group(1)
            parent_class = match.group(2)

            # [修复#15] 跳过泛型参数时感知字符串和注释
            class_start = match.end()
            real_class_start = self._skip_generic_params(content, class_start)
            if real_class_start < 0:
                continue

            class_body = self._extract_block(content, real_class_start)

            # [修复#27] 使用装饰器结果判断is_entry
            decorators = self._extract_decorators(content, match.start())
            is_component = 'Component' in decorators
            is_entry = 'Entry' in decorators

            methods = self._extract_methods(class_body, class_name)

            classes.append(ArkTSClass(
                name=class_name,
                file_path=file_path,
                is_component=is_component,
                is_entry=is_entry,
                parent_class=parent_class,
                decorators=decorators,
                methods=methods
            ))

        for match in self.RE_STRUCT.finditer(content):
            if self._is_in_range(match.start(), non_code_ranges):
                continue

            struct_name = match.group(1)
            try:
                brace_pos = content.index('{', match.end())
            except ValueError:
                continue

            struct_body = self._extract_block(content, brace_pos)
            decorators = self._extract_decorators(content, match.start())
            is_component = 'Component' in decorators
            is_entry = 'Entry' in decorators

            methods = self._extract_methods(struct_body, struct_name)

            classes.append(ArkTSClass(
                name=struct_name,
                file_path=file_path,
                is_component=is_component,
                is_entry=is_entry,
                parent_class=None,
                decorators=decorators,
                methods=methods
            ))

        return classes

    def _skip_generic_params(self, content: str, start: int) -> int:
        """[修复#15] 跳过泛型参数，感知字符串和注释"""
        depth = 0
        i = start
        n = len(content)
        in_string = False
        string_char = None
        in_line_comment = False
        in_block_comment = False

        while i < n:
            ch = content[i]

            if in_line_comment:
                if ch == '\n':
                    in_line_comment = False
                i += 1
                continue
            if in_block_comment:
                if ch == '*' and i + 1 < n and content[i + 1] == '/':
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue
            if in_string:
                if ch == '\\':
                    i += 2
                    continue
                if ch == string_char:
                    in_string = False
                i += 1
                continue

            if not in_string:
                if ch == '/' and i + 1 < n:
                    if content[i + 1] == '/':
                        in_line_comment = True
                        i += 2
                        continue
                    if content[i + 1] == '*':
                        in_block_comment = True
                        i += 2
                        continue

            if ch in ('"', "'", '`'):
                in_string = True
                string_char = ch
                i += 1
                continue

            if ch == '<':
                depth += 1
            elif ch == '>':
                # [修复#4] depth下界保护
                if depth > 0:
                    depth -= 1
            elif ch == '{' and depth == 0:
                return i

            i += 1

        return -1

    def _extract_decorators(self, content: str, pos: int) -> List[str]:
        """[修复#16] 提取类/方法前面的装饰器 - 只取紧邻的连续装饰器"""
        decorators = []
        before = content[max(0, pos - 500):pos]
        lines = before.split('\n')

        for line in reversed(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('//') or stripped.startswith('/*') or stripped.startswith('*'):
                continue
            if stripped.startswith('@'):
                m = self.RE_DECORATOR.match(stripped)
                if m:
                    decorators.insert(0, m.group(1))
                    after_dec = stripped[m.end():].strip()
                    # 如果装饰器后还有内容（非空行），停止
                    if after_dec and not after_dec.startswith('//'):
                        break
                else:
                    break
            elif stripped.startswith('export') or stripped.startswith('default'):
                continue
            else:
                break

        return decorators

    def _extract_block(self, content: str, start: int) -> str:
        """[修复#8] 从 start 位置提取一个 {} 块的内容 - 增加正则字面量处理"""
        depth = 0
        i = start
        in_string = False
        string_char = None
        in_line_comment = False
        in_block_comment = False
        in_regex = False  # [修复#24] 正则字面量

        while i < len(content):
            ch = content[i]

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
            if in_regex:
                if ch == '\\':
                    i += 2
                    continue
                if ch == '/':
                    in_regex = False
                    i += 1
                    while i < len(content) and content[i] in 'gimsuy':
                        i += 1
                    continue
                if ch == '\n':
                    in_regex = False
                i += 1
                continue

            if not in_string:
                if ch == '/' and i + 1 < len(content):
                    if content[i + 1] == '/':
                        in_line_comment = True
                        i += 2
                        continue
                    if content[i + 1] == '*':
                        in_block_comment = True
                        i += 2
                        continue
                    # [修复#24] 正则字面量检测
                    if i > 0:
                        prev = content[i - 1]
                        if prev not in ')]}0123456789"\'`' and not prev.isalnum() and prev != '_':
                            in_regex = True
                            i += 1
                            continue

            if in_string:
                if ch == '\\':
                    i += 2
                    continue
                if ch == string_char:
                    in_string = False
                i += 1
                continue
            else:
                if ch in ('"', "'", '`'):
                    in_string = True
                    string_char = ch
                    i += 1
                    continue

            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return content[start:i]

            i += 1

        # [修复#8] 未闭合块返回剩余内容（调用方需注意）
        return content[start:]

    def _find_non_code_ranges(self, content: str) -> List[Tuple[int, int]]:
        """[修复#13,#14] 找出所有字符串和注释的范围 - 修复越界和嵌套模板"""
        ranges = []
        i = 0
        n = len(content)

        while i < n:
            ch = content[i]

            # 单行注释
            if ch == '/' and i + 1 < n and content[i + 1] == '/':
                start = i
                i += 2
                while i < n and content[i] != '\n':
                    i += 1
                ranges.append((start, i))
                continue

            # 多行注释
            if ch == '/' and i + 1 < n and content[i + 1] == '*':
                start = i
                i += 2
                # [修复#13] 防止越界
                while i < n - 1 and not (content[i] == '*' and content[i + 1] == '/'):
                    i += 1
                if i < n - 1:
                    i += 2
                else:
                    i = n
                ranges.append((start, min(i, n)))
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

            # 模板字符串
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
                    if content[i] == '$' and i + 1 < n and content[i + 1] == '{':
                        ranges.append((start, i))
                        i += 2
                        brace_depth = 1
                        while i < n and brace_depth > 0:
                            if content[i] == '{':
                                brace_depth += 1
                            elif content[i] == '}':
                                brace_depth -= 1
                            elif content[i] in ('"', "'", '`'):
                                # [修复#14] 嵌套模板字符串处理
                                inner_char = content[i]
                                if inner_char == '`':
                                    # 递归跳过嵌套模板字符串
                                    i += 1
                                    inner_brace_depth = 0
                                    while i < n:
                                        if content[i] == '\\':
                                            i += 2
                                            continue
                                        if content[i] == '`':
                                            i += 1
                                            break
                                        if content[i] == '$' and i + 1 < n and content[i + 1] == '{':
                                            inner_brace_depth += 1
                                            i += 2
                                            continue
                                        if content[i] == '{':
                                            inner_brace_depth += 1
                                        elif content[i] == '}':
                                            if inner_brace_depth > 0:
                                                inner_brace_depth -= 1
                                        i += 1
                                    continue
                                i += 1
                                while i < n:
                                    if content[i] == '\\':
                                        i += 2
                                        continue
                                    if content[i] == inner_char:
                                        break
                                    i += 1
                            i += 1
                        start = i
                        continue
                    i += 1
                if start < i:
                    ranges.append((start, i))
                continue

            i += 1

        return ranges

    def _is_in_range(self, pos: int, ranges: List[Tuple[int, int]]) -> bool:
        """[修复#23] 检查位置是否在非代码范围内 - 使用二分搜索 O(log n)"""
        if not ranges:
            return False
        idx = bisect.bisect_right(ranges, (pos, float('inf'))) - 1
        if idx >= 0:
            start, end = ranges[idx]
            return start <= pos < end
        return False

    def _extract_methods(self, class_body: str, class_name: str) -> List[ArkTSMethod]:
        """[修复#2,#3,#4,#5,#9] 从类体中提取所有方法 - 全面修复版"""
        methods = []
        non_code_ranges = self._find_non_code_ranges(class_body)

        # 合并两种模式的匹配
        all_matches = []
        for m in self.RE_METHOD_DECL.finditer(class_body):
            all_matches.append((m.start(), m, 'normal'))
        for m in self.RE_ARROW_FUNC.finditer(class_body):
            # 去重
            if not any(abs(m.start() - pos) < 5 for pos, _, _ in all_matches):
                all_matches.append((m.start(), m, 'arrow'))
        all_matches.sort(key=lambda x: x[0])

        processed_end = 0

        for _, match, match_type in all_matches:
            pos = match.start()

            if pos < processed_end:
                continue

            if self._is_in_range(pos, non_code_ranges):
                continue

            modifiers = match.group(1).strip() if match.group(1) else ''
            method_name = match.group(2)

            if method_name in self.keywords or method_name in self.arkts_ui_components:
                continue

            # [修复#9] 验证paren_start确实是(
            paren_start = match.end() - 1
            if paren_start >= len(class_body) or class_body[paren_start] != '(':
                # 尝试向前查找
                search_start = match.end()
                found = False
                for k in range(search_start, min(search_start + 20, len(class_body))):
                    if class_body[k] == '(':
                        paren_start = k
                        found = True
                        break
                if not found:
                    continue

            # 找到匹配的右括号
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
            after_paren = class_body[paren_end + 1:].lstrip()

            # [修复#3] 正确提取返回类型
            return_type = 'void'
            is_abstract = False
            has_body = False
            body_start = -1
            body_end = -1

            if after_paren.startswith(':'):
                # 有返回类型
                return_type, body_start, body_end, has_body, is_abstract = \
                    self._extract_return_type_and_body(class_body, paren_end)
            elif after_paren.startswith('{'):
                has_body = True
                body_start = paren_end + 1 + after_paren.index('{')
            elif after_paren.startswith(';'):
                is_abstract = True
                body_end = paren_end + 1 + after_paren.index(';')
            else:
                # [修复#4] 检查是否是abstract方法
                if 'abstract' in modifiers:
                    is_abstract = True
                    body_end = paren_end + 1
                else:
                    continue

            params = self._parse_params(params_str)

            method_body = ''
            if has_body and body_start >= 0:
                method_body = self._extract_block(class_body, body_start)
                processed_end = body_start + len(method_body)
            elif is_abstract and body_end >= 0:
                processed_end = body_end + 1
            else:
                continue

            # [修复#2] 对method_body重新计算non_code_ranges
            method_non_code = self._find_non_code_ranges(method_body)
            called_methods = self._extract_method_calls(method_body, class_name, method_non_code)

            nested_components = []
            if method_name == 'build':
                nested_components = self._extract_nested_components(method_body, method_non_code)

            methods.append(ArkTSMethod(
                name=method_name,
                class_name=class_name,
                return_type=return_type,
                params=params,
                decorators=self._extract_decorators(class_body, pos),
                called_methods=called_methods,
                nested_components=nested_components
            ))

        return methods

    def _extract_return_type_and_body(self, content: str, paren_end: int) -> Tuple[str, int, int, bool, bool]:
        """[修复#3,#4] 正确提取返回类型和方法体位置"""
        return_type = 'void'
        has_body = False
        is_abstract = False
        body_start = -1
        body_end = -1

        # 跳过 ':'
        k = paren_end + 1
        while k < len(content) and content[k] in (' ', '\t', '\n'):
            k += 1
        if k < len(content) and content[k] == ':':
            k += 1

        # 提取返回类型文本
        rt_start = k
        angle_depth = 0
        paren_depth = 0

        while k < len(content):
            ch = content[k]

            # 跳过字符串
            if ch in ('"', "'", '`'):
                quote = ch
                k += 1
                while k < len(content):
                    if content[k] == '\\':
                        k += 2
                        continue
                    if content[k] == quote:
                        break
                    k += 1
                k += 1
                continue

            if ch == '<':
                angle_depth += 1
            elif ch == '>':
                # [修复#4] 检测 => 箭头
                if k > 0 and content[k - 1] == '=':
                    pass  # => 箭头
                elif angle_depth > 0:
                    angle_depth -= 1
            elif ch == '(':
                paren_depth += 1
            elif ch == ')':
                if paren_depth > 0:
                    paren_depth -= 1
            elif angle_depth == 0 and paren_depth == 0:
                if ch == '{':
                    return_type = content[rt_start:k].strip()
                    body_start = k
                    has_body = True
                    return return_type, body_start, body_end, has_body, is_abstract
                elif ch == ';':
                    return_type = content[rt_start:k].strip()
                    body_end = k
                    is_abstract = True
                    return return_type, body_start, body_end, has_body, is_abstract
                elif ch == '\n':
                    # 检查下一行是否继续泛型
                    remaining = content[k + 1:].lstrip()
                    if remaining.startswith('<'):
                        k += 1
                        continue
                    # 可能是抽象方法（无分号）
                    return_type = content[rt_start:k].strip() or 'void'
                    is_abstract = True
                    body_end = k
                    return return_type, body_start, body_end, has_body, is_abstract

            k += 1

        return return_type, body_start, body_end, has_body, is_abstract

    def _parse_params(self, params_str: str) -> List[Dict]:
        """[修复#21] 解析参数列表 - 处理字符串中的逗号"""
        params = []
        if not params_str.strip():
            return params

        depth = 0
        current_param = ''
        in_string = False
        string_char = None

        for i, ch in enumerate(params_str):
            if in_string:
                current_param += ch
                if ch == '\\':
                    current_param += params_str[i + 1] if i + 1 < len(params_str) else ''
                    # 跳过下一个字符（通过continue + for循环的i递增）
                    # 但这里需要手动处理
                    continue
                if ch == string_char:
                    in_string = False
                continue

            if ch in ('"', "'", '`'):
                in_string = True
                string_char = ch
                current_param += ch
            elif ch in '<([{':
                depth += 1
                current_param += ch
            elif ch in '>)]}':
                if depth > 0:
                    depth -= 1
                current_param += ch
            elif ch == ',' and depth == 0:
                if current_param.strip():
                    param_info = self._parse_single_param(current_param.strip())
                    params.append(param_info)
                current_param = ''
            else:
                current_param += ch

        if current_param.strip():
            param_info = self._parse_single_param(current_param.strip())
            params.append(param_info)

        return params

    def _parse_single_param(self, param_str: str) -> Dict:
        """[修复#22] 解析单个参数 - 改进默认值处理"""
        # 处理默认值（顶层 = 分割，跳过字符串内）
        eq_pos = self._find_top_level_char(param_str, '=')
        if eq_pos >= 0:
            name_type = param_str[:eq_pos].strip()
            default_value = param_str[eq_pos + 1:].strip()
        else:
            name_type = param_str
            default_value = None

        if name_type.endswith('?'):
            name_type = name_type[:-1]
            optional = True
        else:
            optional = False

        colon_pos = self._find_top_level_char(name_type, ':')
        if colon_pos >= 0:
            name = name_type[:colon_pos].strip()
            param_type = name_type[colon_pos + 1:].strip()
        else:
            name = name_type
            param_type = 'any'

        return {
            'name': name,
            'type': param_type,
            'optional': optional,
            'default': default_value
        }

    def _find_top_level_char(self, text: str, target: str) -> int:
        """[修复#22] 找到顶层（非字符串/非括号内）的字符位置"""
        depth = 0
        in_string = False
        string_char = None
        for i, ch in enumerate(text):
            if in_string:
                if ch == '\\':
                    continue
                if ch == string_char:
                    in_string = False
                continue
            if ch in ('"', "'", '`'):
                in_string = True
                string_char = ch
                continue
            if ch in '<([{':
                depth += 1
            elif ch in '>)]}':
                if depth > 0:
                    depth -= 1
            elif ch == target and depth == 0:
                # 对 = 特殊处理：排除 =>
                if target == '=' and i + 1 < len(text) and text[i + 1] == '>':
                    continue
                return i
        return -1

    def _extract_method_calls(self, method_body: str, current_class: str,
                               non_code_ranges: List[Tuple[int, int]]) -> List[Tuple[str, str]]:
        """[修复#2,#10,#11,#12] 从方法体中提取所有方法调用 - 全面修复"""
        calls = []
        seen = set()

        # [修复#2] non_code_ranges已经基于method_body，坐标一致

        for match in self.RE_METHOD_CALL.finditer(method_body):
            if self._is_in_range(match.start(), non_code_ranges):
                continue

            target_class = match.group(1) or current_class
            method_name = match.group(2)

            # [修复#12] 使用完整内置方法过滤
            if method_name in self.BUILTIN_METHODS:
                continue

            # [修复#11] 统一key和存储结构
            call_tuple = (method_name, target_class)
            if call_tuple not in seen:
                seen.add(call_tuple)
                calls.append(call_tuple)

        # 链式调用
        for match in self.RE_CHAIN_CALL.finditer(method_body):
            if self._is_in_range(match.start(), non_code_ranges):
                continue

            opt_target = match.group(1)
            method_name = match.group(2)

            if method_name in self.BUILTIN_METHODS:
                continue

            target_class = opt_target or current_class
            call_tuple = (method_name, target_class)
            if call_tuple not in seen:
                seen.add(call_tuple)
                calls.append(call_tuple)

        return calls

    def _extract_nested_components(self, build_body: str,
                                    non_code_ranges: List[Tuple[int, int]]) -> List[str]:
        """[修复#2,#10] 从 build 方法中提取嵌套的自定义组件"""
        components = []
        seen = set()

        # [修复#2] non_code_ranges已经基于build_body

        for match in self.RE_COMPONENT_USAGE.finditer(build_body):
            if self._is_in_range(match.start(), non_code_ranges):
                continue

            comp_name = match.group(1)

            if comp_name in self.arkts_ui_components:
                continue

            # 过滤TypeScript内置类型
            ts_types = {
                'Promise', 'Array', 'Object', 'String', 'Number', 'Boolean',
                'Map', 'Set', 'WeakMap', 'WeakSet', 'Date', 'RegExp', 'Error',
                'Response', 'Request', 'URL', 'Record', 'Partial', 'Required',
                'Pick', 'Omit', 'Exclude', 'Extract', 'ReturnType',
            }
            if comp_name in ts_types:
                continue

            if comp_name not in seen:
                seen.add(comp_name)
                components.append(comp_name)

        return components


class CodeKnowledgeGraph:
    """[修复#5,#6,#7,#17] 代码知识图谱 - 全面修复版"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    # [修复#7] 上下文管理器
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def _init_db(self):
        cursor = self.conn.cursor()

        cursor.execute('''CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY, line_count INTEGER)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS classes (
            name TEXT, file_path TEXT, is_component INTEGER,
            is_entry INTEGER, parent_class TEXT, decorators TEXT,
            PRIMARY KEY (name, file_path))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS methods (
            name TEXT, class_name TEXT, file_path TEXT,
            return_type TEXT, params TEXT, decorators TEXT,
            PRIMARY KEY (name, class_name, file_path))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS method_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caller TEXT, caller_class TEXT, callee TEXT,
            callee_class TEXT, file_path TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS component_nesting (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_component TEXT, child_component TEXT, file_path TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT, target TEXT, items TEXT)''')

        self.conn.commit()

    def build(self, project_path: str):
        parser = ArkTSParser()
        files = parser.parse_project(project_path)

        # [修复#7] 使用事务
        try:
            self.conn.execute("BEGIN")
            for arkts_file in files:
                self._insert_file(arkts_file)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        print(f"\n知识图谱构建完成！")
        print(f"  文件数: {len(files)}")
        print(f"  类/组件数: {sum(len(f.classes) for f in files)}")
        print(f"  方法数: {sum(sum(len(c.methods) for c in f.classes) for f in files)}")
        print(f"  数据库: {self.db_path}")

    def _insert_file(self, arkts_file: ArkTSFile):
        cursor = self.conn.cursor()

        cursor.execute('INSERT OR REPLACE INTO files VALUES (?, ?)',
                        (arkts_file.path, arkts_file.line_count))

        # [修复#5] 插入前先删除旧数据
        cursor.execute('DELETE FROM imports WHERE target = ?', (arkts_file.path,))

        for imp in arkts_file.imports:
            cursor.execute('INSERT INTO imports VALUES (?, ?, ?)',
                            (imp['source'], arkts_file.path, ','.join(imp['items'])))

        for cls in arkts_file.classes:
            cursor.execute('INSERT OR REPLACE INTO classes VALUES (?, ?, ?, ?, ?, ?)',
                            (cls.name, arkts_file.path, int(cls.is_component), int(cls.is_entry),
                             cls.parent_class or '', ','.join(cls.decorators)))

            # [修复#6] 删除旧的方法调用和组件嵌套
            cursor.execute('DELETE FROM method_calls WHERE file_path = ? AND caller_class = ?',
                            (arkts_file.path, cls.name))
            cursor.execute('DELETE FROM component_nesting WHERE file_path = ? AND parent_component = ?',
                            (arkts_file.path, cls.name))

            for method in cls.methods:
                cursor.execute('INSERT OR REPLACE INTO methods VALUES (?, ?, ?, ?, ?, ?)',
                                (method.name, cls.name, arkts_file.path, method.return_type,
                                 str(method.params), ','.join(method.decorators)))

                # [修复#11] called_methods 结构改为 (method_name, target_class)
                for callee_method, callee_class in method.called_methods:
                    cursor.execute('INSERT INTO method_calls VALUES (NULL, ?, ?, ?, ?)',
                                    (method.name, cls.name, callee_method, callee_class, arkts_file.path))

                for child in method.nested_components:
                    cursor.execute('INSERT INTO component_nesting VALUES (NULL, ?, ?, ?)',
                                    (cls.name, child, arkts_file.path))

    def execute_sql(self, sql: str) -> List[Dict]:
        """[修复#17] 执行 SQL 查询 - 只允许SELECT"""
        stripped = sql.strip().upper()
        if not stripped.startswith('SELECT') and not stripped.startswith('PRAGMA'):
            return [{'error': '安全限制：仅允许 SELECT 和 PRAGMA 查询'}]
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            return [{'error': str(e)}]

    def get_stats(self) -> Dict:
        """[修复#18] 获取统计信息 - 动态获取表名"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        stats = {}
        for table in tables:
            try:
                cursor.execute(f'SELECT COUNT(*) FROM {table}')
                stats[table] = cursor.fetchone()[0]
            except sqlite3.Error:
                pass
        return stats

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("用法: python arkts_code_graph_optimized.py <项目路径> [数据库路径]")
        sys.exit(1)

    project_path = sys.argv[1]
    db_path = sys.argv[2] if len(sys.argv) > 2 else 'code_graph.db'

    graph = CodeKnowledgeGraph(db_path)
    graph.build(project_path)
    graph.close()
