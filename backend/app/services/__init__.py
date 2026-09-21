"""业务服务层：Dify 编排、解析、审核、出题、问答、存储等。

模块划分：
- `dify/`               —— Dify 集成层（http / mock 双实现，统一契约）
- `moderation/`         —— 内容审核（本地规则 + 阿里云内容安全）
- `support.py`          —— 分页 / ID 反查 / 用量落库等公共工具
- `auth_service.py`     —— 认证与用户
- `kb_service.py`       —— 知识库与成员权限
- `document_service.py` —— 文档上传、状态、预览、重试、删除
- `chat_service.py`     —— 问答会话与 SSE 事件流
- `quiz_service.py`     —— 出题、判分、错题联动
- `wrong_book_service.py` —— 错题本
- `ops_service.py`      —— 审核日志与用量统计
- `task_service.py`     —— 异步任务队列（入队/领取/重试）
"""

