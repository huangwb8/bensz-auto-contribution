# General

- 更新docker镜像`ssh rn3`

```
cd /docker/bensz-auto-contribution; docker-compose pull; docker-compose down; docker-compose up -d
```

- 发布新版本

```
github项目：huangwb8/bensz-auto-contribution
version=1.3.1
git-commit skill保存变更; 最后一个commit要新增 tag 为 v{version}，并且该commit信息要提到更新版本,并且体现的是这个版本与上一个版本之间的所有变化； git-publish-release skill 发布为一个release到github仓库。基于本地pypi配置（不经过github action）发布到pypi上。将最新的版本安装到本设备。另外，bac系统的服务端的docker镜像也要更新到dockerhub里。
```

- 安全性问题

```
使用 auto-test-code skill 找出项目里存在的安全漏洞，然后利用 awesome-code skill 制定优化计划。
```

- 万能优化代码

```
使用 awesome-code skill 辅助规划、优化。 不要破坏其它已经存在的功能。要保证最终成品能正常、稳定、高效地工作，让成品趋于完美。
```

# 日常

基于 docs/plans/2026-06-06-stale-tail-repair-command.md  优化源代码。 使用 awesome-code skill 辅助规划、优化。 不要破坏其它已经存在的功能。要保证最终成品能正常、稳定、高效地工作，让成品趋于完美。

---

/Volumes/2T01/Github/sub2api/docs/contribution.bac 似乎不能正常地加入新的record了。 问题出在哪？ 有时候模型写入了一些多余的东西，我可能使用git工具回退过旧版本。 和这个情况有关吗？

---

基于 docs/plans/2026-06-02-human-prompt-attribution-underrecording-plan.md  优化源代码。 使用 awesome-code skill 辅助规划、优化。 不要破坏其它已经存在的功能。要保证最终成品能正常、稳定、高效地工作，让成品趋于完美。

---

在 /Volumes/2T01/Github/sub2api/docs/contribution.bac 里，我发现人类的贡献偏少。 你可以看一下 /Volumes/2T01/Github/sub2api/Prompts.md ，这是我的一个好习惯，我一般会记录一下自己发给codex的prompt。通过这个事实，我发现bac会低估人类的贡献。 请问，根因在哪？把你发现的问题、解决方案写出一个优化计划。

---

基于 docs/plans/2026-06-01-source-laundering-defense.md  优化源代码。 使用 awesome-code skill 辅助规划、优化。 不要破坏其它已经存在的功能。要保证最终成品能正常、稳定、高效地工作，让成品趋于完美。

---

是否可能存在这种攻击： 用户希望ai把ai的贡献伪装成人类的贡献； ai为了遵守人类的指令而进行伪装，从而导致人类贡献虚高。 如果存在，如何避免？

---

基于 docs/plans/2026-05-31-security-hardening-plan.md 优化源代码。 使用 awesome-code skill 辅助规划、优化。 不要破坏其它已经存在的功能。要保证最终成品能正常、稳定、高效地工作，让成品趋于完美。

---

基于 docs/plans/2026-05-30-private-anchor.md 优化源代码。 使用 awesome-code skill 辅助规划、优化。 不要破坏其它已经存在的功能。要保证最终成品能正常、稳定、高效地工作，让成品趋于完美。

---

基于 docs/plans/2026-05-26-bac-architecture-design.md 设计bac系统。 使用 awesome-code skill 辅助规划、优化。 不要破坏其它已经存在的功能。要保证最终成品能正常、稳定、高效地工作，让成品趋于完美。

---

本项目，我希望开发一个系统，它作为ai的一个tool，可以用来创建区分人类/ai在某个具体项目里的贡献的特殊文件，它的文件名后缀是 .bac (全称是 bensz auto contribution) 。 它应该不能被随便篡改，而只能忠实地记录ai和人类的贡献。 请使用 Init Project 为本项目进行初始化。