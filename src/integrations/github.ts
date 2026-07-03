/**
 * GitHub Integration — Real PR creation via Octokit.
 *
 * Falls back to in-memory PR objects when GITHUB_TOKEN is not set.
 */

import { Octokit } from "@octokit/rest";
import type { PullRequest, DriftAnalysis } from "../types.js";

let _octokit: Octokit | null = null;

function getOctokit(): Octokit | null {
  const token = process.env.GITHUB_TOKEN;
  if (!token || token === "YOUR_GITHUB_TOKEN" || token === "") return null;
  if (!_octokit) {
    _octokit = new Octokit({ auth: token });
  }
  return _octokit;
}

export function isGitHubConfigured(): boolean {
  return getOctokit() !== null;
}

export interface GitHubPROptions {
  repo: string;       // "owner/repo"
  baseBranch: string; // e.g. "main"
  resourceName: string;
  resourceType: string;
  branchName: string;
  prTitle: string;
  prDescription: string;
  hclChanges: string;
  analysis: DriftAnalysis;
}

export interface GitHubPRResult {
  success: boolean;
  prNumber?: number;
  prUrl?: string;
  branchName?: string;
  error?: string;
  simulated: boolean;
}

export async function createPullRequest(opts: GitHubPROptions): Promise<GitHubPRResult> {
  const octokit = getOctokit();
  const [owner, repoName] = opts.repo.split("/");

  if (!octokit || !owner || !repoName) {
    // Fallback: return simulated result
    console.log(`[github] GITHUB_TOKEN not set — returning simulated PR for ${opts.resourceName}`);
    return {
      success: true,
      prNumber: Math.floor(Math.random() * 9000) + 1000,
      prUrl: `https://github.com/${opts.repo}/pull/simulated`,
      branchName: opts.branchName,
      simulated: true,
    };
  }

  try {
    // 1. Get the base branch SHA
    const baseRef = await octokit.git.getRef({
      owner,
      repo: repoName,
      ref: `heads/${opts.baseBranch}`,
    });
    const baseSha = baseRef.data.object.sha;

    // 2. Create a new branch
    try {
      await octokit.git.createRef({
        owner,
        repo: repoName,
        ref: `refs/heads/${opts.branchName}`,
        sha: baseSha,
      });
    } catch (err: any) {
      if (err.status === 422) {
        // Branch already exists — force-update it
        const existingRef = await octokit.git.getRef({
          owner,
          repo: repoName,
          ref: `heads/${opts.branchName}`,
        });
        await octokit.git.updateRef({
          owner,
          repo: repoName,
          ref: `heads/${opts.branchName}`,
          sha: baseSha,
          force: true,
        });
      } else {
        throw err;
      }
    }

    // 3. Create a blob with the HCL changes
    const blobResp = await octokit.git.createBlob({
      owner,
      repo: repoName,
      content: opts.hclChanges,
      encoding: "utf-8",
    });

    // 4. Get the current tree
    const baseCommit = await octokit.git.getCommit({
      owner,
      repo: repoName,
      commit_sha: baseSha,
    });
    const treeSha = baseCommit.data.tree.sha;

    // 5. Create a new tree with the reconcile file
    const newTree = await octokit.git.createTree({
      owner,
      repo: repoName,
      base_tree: treeSha,
      tree: [
        {
          path: `reconcile_${opts.resourceName}.tf`,
          mode: "100644",
          type: "blob",
          sha: blobResp.data.sha,
        },
      ],
    });

    // 6. Create the commit
    const commitResp = await octokit.git.createCommit({
      owner,
      repo: repoName,
      message: opts.prTitle,
      tree: newTree.data.sha,
      parents: [baseSha],
    });

    // 7. Update the branch ref to point to the new commit
    await octokit.git.updateRef({
      owner,
      repo: repoName,
      ref: `heads/${opts.branchName}`,
      sha: commitResp.data.sha,
    });

    // 8. Create the pull request
    const prResp = await octokit.pulls.create({
      owner,
      repo: repoName,
      title: opts.prTitle,
      head: opts.branchName,
      base: opts.baseBranch,
      body: opts.prDescription,
    });

    console.log(`[github] PR created: ${prResp.data.html_url}`);
    return {
      success: true,
      prNumber: prResp.data.number,
      prUrl: prResp.data.html_url,
      branchName: opts.branchName,
      simulated: false,
    };
  } catch (err: any) {
    console.error(`[github] PR creation failed: ${err.message}`);
    return {
      success: false,
      error: err.message,
      simulated: false,
    };
  }
}

export async function mergePullRequest(
  repo: string,
  prNumber: number
): Promise<{ success: boolean; error?: string }> {
  const octokit = getOctokit();
  if (!octokit) {
    console.log(`[github] Simulated merge of PR #${prNumber}`);
    return { success: true };
  }

  const [owner, repoName] = repo.split("/");
  try {
    await octokit.pulls.merge({
      owner,
      repo: repoName,
      pull_number: prNumber,
      merge_method: "squash",
    });
    return { success: true };
  } catch (err: any) {
    return { success: false, error: err.message };
  }
}
