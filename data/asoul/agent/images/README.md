# 图片库（agent 主题图用）

图片库 = 按主题分类的图片（壁纸、美图、梗图等），与表情包库相互独立。
agent 在用户明确想看图（"来张图""发张壁纸"）时通过 `send_image` 工具按主题挑选发送。

## 用法

- **一级子目录 = 分类**，直接把图片丢进对应分类目录即可，**无需重启**：

```
images/
  壁纸/
    海边日落.png
  梗图/
    deploy_meme.jpg
```

- 支持 png / jpg / jpeg / gif / webp / bmp。

## 标签（agent 怎么找到你的图）

- **默认**：分类目录名 + 文件名拆词都是标签。`壁纸/海边日落.png` 生成标签
  `壁纸`、`海边日落`；`梗图/deploy_meme.jpg` 生成 `梗图`、`deploy`、`meme`。
- **精确控制**（可选）：在上级目录的 `images_index.json` 里登记，key 为库内相对路径：

```json
{
  "壁纸/海边日落.png": {"tags": ["壁纸", "风景", "海边"], "desc": "海边黄昏"}
}
```

图片首次发送时自动上传 COS（分类目录名会拼进 key：`static/agent/image/<分类>/<文件名>`），
之后走 manifest 缓存，重复发送零上传。

## 相关

- 表情包库（聊天情绪小图）在 `../stickers/`，扁平目录无分类。
- SUPERUSER 可用 `/图床同步 static/agent/image` 批量预上传（含分类子目录）。
