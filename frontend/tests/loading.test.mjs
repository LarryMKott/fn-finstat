/* 全局任务进度（composables/useLoading.js）行为自测。
 *
 * 关注三件事：四态反馈、防重入、以及**不会卡死**（任何路径最终都会收起浮层）。
 * 不引入测试框架：node --test 在部分环境不可用，这里用最朴素的断言 + 计数。
 *
 * 运行： npm test   （等价于 node tests/loading.test.mjs）
 */
import assert from "node:assert/strict";

import {
  dismissLoading,
  isBusy,
  loadingState,
  runTask,
  TASK_CANCELLED,
} from "../src/composables/useLoading.js";

/* 让测试不必真的等 1.3 秒：所有用例都把停留时长压到接近 0 */
const FAST = { minVisible: 0, successHold: 5, errorHold: 10 };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
/* 等到浮层收起，超时即视为卡死 */
async function waitHidden(timeout = 2000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (!loadingState.visible) return true;
    await sleep(10);
  }
  return false;
}

let passed = 0;
const failures = [];

async function test(name, fn) {
  dismissLoading();
  await sleep(20);
  try {
    await fn();
    passed += 1;
    console.log(`  ok   ${name}`);
  } catch (err) {
    failures.push({ name, err });
    console.log(`  FAIL ${name}\n       ${err.message}`);
  }
}

console.log("useLoading");

await test("成功任务：running → success → 最终自动收起（不卡死）", async () => {
  const seen = [];
  const res = await runTask({
    key: "t:ok",
    title: "成功任务",
    detail: "开始",
    ...FAST,
    task: async (update) => {
      seen.push(loadingState.phase);
      update({ detail: "处理中…", progress: 50 });
      assert.equal(loadingState.detail, "处理中…");
      assert.equal(loadingState.progress, 50);
      return 42;
    },
  });
  assert.equal(res, 42);
  assert.deepEqual(seen, ["running"]);
  assert.equal(loadingState.phase, "success");
  assert.equal(loadingState.title, "成功任务");
  assert.ok(await waitHidden(), "浮层未自动收起 = 卡死");
});

await test("失败任务：展示错误原因，且默认原样抛出", async () => {
  let caught = null;
  try {
    await runTask({
      key: "t:fail",
      title: "失败任务",
      ...FAST,
      task: async () => {
        throw new Error("磁盘已满");
      },
    });
  } catch (err) {
    caught = err;
  }
  assert.ok(caught, "错误应原样抛给业务侧");
  assert.equal(caught.message, "磁盘已满");
  assert.equal(loadingState.phase, "error");
  assert.equal(loadingState.error, "磁盘已满");
  assert.equal(loadingState.detail, "磁盘已满");
  assert.ok(await waitHidden(), "失败后也应自动收起");
});

await test("rethrow=false：不抛给调用方，但错误照样上浮层", async () => {
  const res = await runTask({
    key: "t:swallow",
    title: "静默失败",
    rethrow: false,
    ...FAST,
    task: async () => {
      throw new Error("网络超时");
    },
  });
  assert.equal(res, undefined);
  assert.equal(loadingState.error, "网络超时");
  assert.ok(await waitHidden());
});

await test("防重入：queue 模式下同一 key 的第二次调用被拦截", async () => {
  let calls = 0;
  const opts = {
    key: "t:dedupe",
    title: "写操作",
    ...FAST,
    task: async () => {
      calls += 1;
      await sleep(30);
      return calls;
    },
  };
  const first = runTask(opts);
  assert.equal(isBusy("t:dedupe"), true, "执行中应处于 busy");
  const second = await runTask(opts);
  const firstRes = await first;
  assert.equal(firstRes, 1);
  assert.equal(second, undefined, "重入应被拦截并返回 undefined");
  assert.equal(calls, 1, "底层任务只能执行一次");
  assert.equal(isBusy("t:dedupe"), false, "结束后必须解锁");
  assert.ok(await waitHidden());
});

await test("latest 模式：允许重入，旧任务的状态写入被作废", async () => {
  let releaseOld;
  const oldGate = new Promise((r) => {
    releaseOld = r;
  });
  const old = runTask({
    key: "t:latest",
    title: "旧请求",
    mode: "latest",
    minVisible: 0,
    successHold: 5,
    task: () => oldGate,
  });
  await sleep(10);

  const fresh = await runTask({
    key: "t:latest",
    title: "新请求",
    mode: "latest",
    ...FAST,
    task: async () => "new",
  });
  assert.equal(fresh, "new");
  assert.equal(loadingState.phase, "success");

  /* 旧请求这时才回来：它的结果不允许覆盖新任务的状态 */
  releaseOld("old");
  await old;
  assert.equal(isBusy("t:latest"), false, "新任务结束后必须解锁，不能被旧任务卡住");
  assert.ok(await waitHidden(), "旧任务回来后浮层仍应收起");
});

await test("并发多 key：先完成的不会提前关掉仍在进行的任务", async () => {
  let releaseSlow;
  const slowGate = new Promise((r) => {
    releaseSlow = r;
  });
  const slow = runTask({
    key: "t:slow",
    title: "慢任务",
    mode: "latest",
    minVisible: 0,
    successHold: 5,
    task: () => slowGate,
  });
  await sleep(10);

  await runTask({
    key: "t:quick",
    title: "快任务",
    mode: "latest",
    ...FAST,
    task: async () => "quick",
  });
  /* 刚完成的一瞬间浮层展示的是快任务的完成态，这是预期行为 */
  assert.equal(loadingState.title, "快任务");
  await sleep(40);
  /* 快任务已过停留期被移出栈，慢任务仍在跑 —— 浮层必须回落到它，而不是收起 */
  assert.equal(loadingState.visible, true, "并发时不应被先完成的任务关掉浮层");
  assert.equal(loadingState.title, "慢任务");
  assert.equal(loadingState.phase, "running");

  releaseSlow("slow");
  await slow;
  assert.ok(await waitHidden(), "全部结束后应收起");
});

await test("TASK_CANCELLED：立即收起，不显示成功也不显示失败", async () => {
  const res = await runTask({
    key: "t:cancel",
    title: "可取消任务",
    ...FAST,
    task: async () => TASK_CANCELLED,
  });
  assert.equal(res, TASK_CANCELLED);
  assert.equal(loadingState.visible, false, "取消后浮层应立刻消失");
  assert.equal(loadingState.phase, "idle");
  assert.equal(isBusy("t:cancel"), false);
});

await test("update 支持字符串与对象两种写法", async () => {
  await runTask({
    key: "t:update",
    title: "进度任务",
    ...FAST,
    task: async (update) => {
      update("第一步");
      assert.equal(loadingState.detail, "第一步");
      update({ detail: "第二步", progress: 30, title: "换个名字" });
      assert.equal(loadingState.detail, "第二步");
      assert.equal(loadingState.progress, 30);
      assert.equal(loadingState.title, "换个名字");
      update({ progress: null });
      assert.equal(loadingState.progress, null);
    },
  });
  assert.ok(await waitHidden());
});

await test("dismissLoading 可手动关闭失败卡片", async () => {
  await runTask({
    key: "t:dismiss",
    title: "手动关闭",
    rethrow: false,
    errorHold: 60000,
    task: async () => {
      throw new Error("需要用户确认");
    },
  });
  assert.equal(loadingState.phase, "error");
  dismissLoading();
  assert.equal(loadingState.visible, false, "手动关闭后应立即隐藏");
});

await test("抛异常也要解锁（finally 兜底），不留死锁", async () => {
  await runTask({
    key: "t:unlock",
    title: "异常解锁",
    rethrow: false,
    ...FAST,
    task: async () => {
      throw new Error("boom");
    },
  });
  assert.equal(isBusy("t:unlock"), false);
  /* 同一个 key 立刻可再次执行 */
  const again = await runTask({
    key: "t:unlock",
    title: "重试",
    ...FAST,
    task: async () => "retry-ok",
  });
  assert.equal(again, "retry-ok", "失败后同 key 不应被永久锁死");
  assert.ok(await waitHidden());
});

console.log("");
if (failures.length) {
  console.log(`${passed} passed, ${failures.length} failed`);
  process.exit(1);
}
console.log(`${passed} passed`);
