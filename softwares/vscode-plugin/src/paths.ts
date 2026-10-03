import * as path from 'node:path';
import { realpath } from 'node:fs/promises';

export function inside(root: string, candidate: string): boolean {
  const relative = path.relative(root, candidate);
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}

export async function safeProjectPath(root: string, relative: string): Promise<string> {
  if (!relative || path.isAbsolute(relative) || /^[a-zA-Z]:/.test(relative) || relative.includes('\\') || relative.includes('\0')) {
    throw new Error('记录中的文件路径必须是项目内相对路径。');
  }
  const target = path.resolve(root, relative);
  if (!inside(path.resolve(root), target)) throw new Error('文件路径超出当前项目。');
  const actualRoot = await realpath(root);
  // For deleted/new files, resolve the nearest existing ancestor to detect symlink escapes.
  let ancestor = target;
  while (true) {
    try {
      const actual = await realpath(ancestor);
      if (!inside(actualRoot, actual)) throw new Error('文件路径通过符号链接超出当前项目。');
      return target;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      const parent = path.dirname(ancestor);
      if (parent === ancestor) throw error;
      ancestor = parent;
    }
  }
}
