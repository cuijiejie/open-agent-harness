import multiprocessing

# ip端口
bind = "0.0.0.0:5001"

# 进程数量
# workers = multiprocessing.cpu_count() * 2 + 1
# 线上非root进程个数有限制，改为固定6个进程
workers = 6

# 每个进程最大线程数
threads = 4

# 工作模式
# 注意：默认的 sync worker **会忽略 threads**（6 进程实际只有 6 并发），
# 而本服务是 SSE 长连接 + 阻塞式沙箱 CLI 调用，必须用 gthread 才吃得到多线程。
# gevent 与代码里的线程池配合有超时问题，不要用。
worker_class = "gthread"

# max_requests = 5000

# 超时时间
# 必须 > 单轮对话上限 turn_max_seconds（dev 1200 / pro 600），否则长任务会被
# gunicorn 直接杀掉：前端断流、收尾逻辑不执行（以前 240 比单轮上限还小）。
timeout = 1300

# 重启时给正在服务的请求留收尾时间（SSE 长连接）
graceful_timeout = 60

# 日志级别
loglevel = "info"

# 访问日志输出文件
accesslog = "open_agent_harness.log"
access_log_format = '%(t)s %(p)s %(h)s "%(r)s" %(s)s %(L)s %(b)s %(f)s" "%(a)s"'

# 错误日志输出文件
errorlog = "open_agent_harness.log"
error_log_format = '%(t)s %(p)s %(h)s "%(r)s" %(s)s %(L)s %(b)s %(f)s" "%(a)s"'

# 控制台打印
capture_output = True

# 后台启动
daemon = True
