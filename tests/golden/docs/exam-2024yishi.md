\section{2024年新领军一试}

\section{新领军一试（2024年11月）}

\begin{problem}[第1题]
证明极限
\[
\lim_{n\to\infty}\int_0^1 n x^n e^x\,dx
\]
存在并求其值。
\end{problem}
\begin{solution}
设
\[
I_n=\int_0^1 n x^n e^x\,dx.
\]
对积分分部积分，取
\[
u=e^x,\qquad dv=n x^n\,dx,
\]
则
\[
du=e^x\,dx,\qquad v=\frac{n}{n+1}x^{n+1}.
\]
于是
\begin{align*}
I_n
&=\left.\frac{n}{n+1}x^{n+1}e^x\right|_{0}^{1}-\frac{n}{n+1}\int_0^1 x^{n+1}e^x\,dx \\
&=\frac{n}{n+1}e-\frac{n}{n+1}J_{n+1},
\end{align*}
其中
\[
J_{n+1}:=\int_0^1 x^{n+1}e^x\,dx.
\]
注意到在 $[0,1]$ 上有 $0\le x^{n+1}\le 1$ 且 $e^x\le e$，因此
\[
0\le J_{n+1}\le e\int_0^1 x^{n+1}\,dx=\frac{e}{n+2}\xrightarrow[n\to\infty]{}0.
\]
从而
\[
\lim_{n\to\infty} I_n
=\lim_{n\to\infty}\left(\frac{n}{n+1}e-\frac{n}{n+1}J_{n+1}\right)
=e-0=e.
\]
更具体地，
\[
|I_n-e|
=\left|-\frac{e}{n+1}-\frac{n}{n+1}J_{n+1}\right|
\le \frac{e}{n+1}+J_{n+1}
\le \frac{e}{n+1}+\frac{e}{n+2}\to 0,
\]
故该极限存在且其值为 $e$。
\end{solution}
\begin{exercise}[变式1]
证明极限
\[
\lim_{n\to\infty}\int_0^1 n x^n \ln(1+x)\,dx
\]
存在并求其值。
\end{exercise}
\begin{solution}
记
\[
I_n=\int_0^1 n x^n \ln(1+x)\,dx.
\]
分部积分，取 $u=\ln(1+x)$，$dv=n x^n\,dx$，则
\[
du=\frac{1}{1+x}\,dx,\qquad v=\frac{n}{n+1}x^{n+1}.
\]
于是
\[
I_n=\left.\frac{n}{n+1}x^{n+1}\ln(1+x)\right|_{0}^{1}
-\frac{n}{n+1}\int_0^1 \frac{x^{n+1}}{1+x}\,dx
=\frac{n}{n+1}\ln 2-\frac{n}{n+1}\int_0^1 \frac{x^{n+1}}{1+x}\,dx.
\]
由于 $0\le \frac1{1+x}\le 1$，从而
\[
0\le \frac{n}{n+1}\int_0^1 \frac{x^{n+1}}{1+x}\,dx
\le \frac{n}{n+1}\int_0^1 x^{n+1}\,dx
=\frac{n}{(n+1)(n+2)}\xrightarrow[n\to\infty]{}0.
\]
因此极限存在，且
\[
\lim_{n\to\infty}I_n=\ln 2.
\]
\end{solution}
\begin{exercise}[变式2]
证明极限
\[
\lim_{n\to\infty}\int_0^1 n(1-x)^n e^{x}\,dx
\]
存在并求其值。
\end{exercise}
\begin{solution}
设
\[
J_n=\int_0^1 n(1-x)^n e^{x}\,dx.
\]
作代换 $t=1-x$（则 $x=1-t,\ dx=-dt$），得
\[
J_n=\int_0^1 n t^n e^{1-t}\,dt
=e\int_0^1 n t^n e^{-t}\,dt.
\]
对内层积分分部积分，取 $u=e^{-t}$，$dv=n t^n\,dt$，则
\[
du=-e^{-t}\,dt,\qquad v=\frac{n}{n+1}t^{n+1}.
\]
于是
\[
\int_0^1 n t^n e^{-t}\,dt
=\left.\frac{n}{n+1}t^{n+1}e^{-t}\right|_0^1
+\frac{n}{n+1}\int_0^1 t^{n+1}e^{-t}\,dt
=\frac{n}{n+1}e^{-1}+\frac{n}{n+1}\int_0^1 t^{n+1}e^{-t}\,dt.
\]
从而
\[
J_n
=e\left(\frac{n}{n+1}e^{-1}+\frac{n}{n+1}\int_0^1 t^{n+1}e^{-t}\,dt\right)
=\frac{n}{n+1}+e\frac{n}{n+1}\int_0^1 t^{n+1}e^{-t}\,dt.
\]
又因 $0<e^{-t}\le 1$，有估计
\[
0\le e\frac{n}{n+1}\int_0^1 t^{n+1}e^{-t}\,dt
\le e\frac{n}{n+1}\int_0^1 t^{n+1}\,dt
=e\frac{n}{(n+1)(n+2)}\xrightarrow[n\to\infty]{}0.
\]
因此极限存在，且
\[
\lim_{n\to\infty}J_n=\lim_{n\to\infty}\frac{n}{n+1}=1.
\]
\end{solution}
\begin{exercise}[变式3]
证明极限
\[
\lim_{n\to\infty}\int_0^1 (n+1)(n+2)\,x^n(1-x)\cos x\,dx
\]
存在并求其值。   
\end{exercise}
\begin{solution}
记
\[
K_n=\int_0^1 (n+1)(n+2)\,x^n(1-x)\cos x\,dx,\qquad
p_n(x)=(n+1)(n+2)x^n(1-x).
\]
先计算归一化：
\[
\int_0^1 p_n(x)\,dx
=(n+1)(n+2)\int_0^1 x^n(1-x)\,dx
=(n+1)(n+2)\Bigl(\frac1{n+1}-\frac1{n+2}\Bigr)=1.
\]
故 $p_n$ 为 $[0,1]$ 上的概率密度。

取任意 $\varepsilon\in(0,1)$，对 $x\in[0,1-\varepsilon]$ 有 $x^n\le(1-\varepsilon)^n$，于是
\[
\int_0^{1-\varepsilon} p_n(x)\,dx
\le (n+1)(n+2)(1-\varepsilon)^n\int_0^{1-\varepsilon}(1-x)\,dx
\le (n+1)(n+2)(1-\varepsilon)^n\xrightarrow[n\to\infty]{}0.
\]
另一方面，$\cos x$ 在 $[0,1]$ 上一致连续：给定任意 $\delta>0$，可取 $\varepsilon>0$ 使得
$|x-1|<\varepsilon \Rightarrow |\cos x-\cos 1|<\delta$。
于是
\[
\begin{aligned}
|K_n-\cos 1|
&=\left|\int_0^1 p_n(x)\bigl(\cos x-\cos 1\bigr)\,dx\right|\\
&\le \int_{1-\varepsilon}^1 p_n(x)\,|\cos x-\cos 1|\,dx
+\int_0^{1-\varepsilon} p_n(x)\,|\cos x-\cos 1|\,dx\\
&\le \delta\int_{1-\varepsilon}^1 p_n(x)\,dx
+2\int_0^{1-\varepsilon} p_n(x)\,dx\\
&\le \delta+2\int_0^{1-\varepsilon} p_n(x)\,dx.
\end{aligned}
\]
令 $n\to\infty$，由上式与 $\int_0^{1-\varepsilon} p_n\to 0$ 得
\[
\limsup_{n\to\infty}|K_n-\cos 1|\le \delta.
\]
$\delta$ 任意，故 $K_n\to \cos 1$。因此极限存在且
\[
\lim_{n\to\infty}K_n=\cos 1.
\]
\end{solution}




\begin{problem}[第2题]
设 $m,n,k$ 是正整数，定义线性映射 $L:M_{m\times n}\to M_{k\times n}$。  
记
\[
W=\{L\mid L(AB)=L(A)B,\ \forall A\in M_{m\times n}, B\in M_{n\times n}\}.
\]
求 $\dim W$。
\end{problem}
\begin{solution}
把 $M_{m\times n}$ 看成右 $M_{n\times n}$-模，模结构为右乘：
\[
A\cdot B:=AB,\quad A\in M_{m\times n},\ B\in M_{n\times n}.
\]
同理，$M_{k\times n}$ 也可看作右 $M_{n\times n}$-模。则条件
\[
L(AB)=L(A)B,\ \forall A,B
\]
正是说 $L$ 是右 $M_{n\times n}$-模同态。于是
\[
W=\mathrm{Hom}_{M_{n\times n}}\bigl(M_{m\times n},M_{k\times n}\bigr).
\]

\medskip
\noindent\textbf{第一步：把 $M_{m\times n}$ 分解为行向量直和.}

记
\[
R:=\mathbb{F}^{1\times n},
\]
其右 $M_{n\times n}$-模结构同样由右乘给出：$r\cdot B:=rB$。

将 $A\in M_{m\times n}$ 写成 $m$ 个行向量
\[
A=\begin{pmatrix}a_1\\ \vdots\\ a_m\end{pmatrix},\quad a_i\in R,
\]
则右乘 $B$ 时，
\[
AB=\begin{pmatrix}a_1B\\ \vdots\\ a_mB\end{pmatrix}.
\]
所以作为右 $M_{n\times n}$-模，有同构
\[
M_{m\times n}\cong R^{\oplus m},\qquad
M_{k\times n}\cong R^{\oplus k}.
\]
于是
\[
W\cong \mathrm{Hom}_{M_{n\times n}}\bigl(R^{\oplus m},R^{\oplus k}\bigr).
\]

对右模直和，有
\[
\mathrm{Hom}_{M_{n\times n}}\bigl(R^{\oplus m},R^{\oplus k}\bigr)
\cong M_{k\times m}\bigl(\mathrm{End}_{M_{n\times n}}(R)\bigr),
\]
即给一个模同态相当于给出一个 $k\times m$ 的“矩阵”，其每个条目是一个 $R\to R$ 的右模同态。

\medskip
\noindent\textbf{第二步：求 $\mathrm{End}_{M_{n\times n}}(R)$ 的维数.}

任取右 $M_{n\times n}$-模同态
\[
\varphi:R\to R,\qquad \varphi(rB)=\varphi(r)B,\ \forall r\in R,B\in M_{n\times n}.
\]
作为线性变换，$\varphi$ 一定可写成右乘一个矩阵：
\[
\exists C\in M_{n\times n},\ \forall r\in R,\quad \varphi(r)=rC.
\]
把这一表达式代入模同态条件：
\[
\varphi(rB)=rBC,\qquad \varphi(r)B=(rC)B=rCB.
\]
于是对任意行向量 $r$、任意 $B$，都有
\[
rBC=rCB.
\]
由于任意行向量 $r$ 都可取到，得到
\[
BC=CB,\quad \forall B\in M_{n\times n},
\]
即 $C$ 与所有 $n\times n$ 矩阵对易。众所周知，与 $M_{n\times n}$ 中所有矩阵对易的矩阵必为标量矩阵：
\[
C=\lambda I_n,\quad \lambda\in\mathbb{F}.
\]
因此
\[
\varphi(r)=rC=r(\lambda I_n)=\lambda r.
\]
也就是说，任何右模同态 $R\to R$ 都是某个标量倍的恒等变换，因而
\[
\dim_{\mathbb{F}}\mathrm{End}_{M_{n\times n}}(R)=1.
\]

\medskip
\noindent\textbf{第三步：计算 $\dim W$.}

由上一步，$\mathrm{End}_{M_{n\times n}}(R)$ 同构于一维的标量空间，每个 $R\to R$ 的右模同态只由一个标量决定。因此
\[
\mathrm{Hom}_{M_{n\times n}}\bigl(R^{\oplus m},R^{\oplus k}\bigr)
\cong M_{k\times m}(\mathbb{F}),
\]
作为线性空间，其维数为
\[
\dim W=\dim M_{k\times m}(\mathbb{F})=km.
\]

顺便说明：这一结果也可以具体描述为：对任意 $L\in W$，存在唯一的 $C\in M_{k\times m}$，使得
\[
L(A)=CA,\quad \forall A\in M_{m\times n},
\]
显然有 $L(AB)=C(AB)=(CA)B=L(A)B$。

综上，
\[
\dim W=km.
\]
\end{solution}
\begin{exercise}[变式1：加上“双边”条件（双模同态）]
设 $m,n$ 为正整数，$T:M_{m\times n}\to M_{m\times n}$ 为线性映射。记
\[
W=\{T\mid T(BAC)=BT(A)C,\ \forall\,A\in M_{m\times n},\ \forall\,B\in M_{m\times m},\ \forall\,C\in M_{n\times n}\}.
\]
求 $\dim W$，并写出所有 $T$ 的形式。
\end{exercise}
\begin{solution}
仍用矩阵单位记号 $E^{(m,n)}_{ij}$，并记 $F_{pq}=E^{(m,m)}_{pq}$、$G_{rs}=E^{(n,n)}_{rs}$。

先对 $A=E_{11}$，取 $B=F_{11}$、$C=I_n$，有 $F_{11}E_{11}=E_{11}$，故
\[
T(E_{11})=T(F_{11}E_{11})=F_{11}T(E_{11}),
\]
从而 $T(E_{11})$ 只有第 $1$ 行可能非零。
同理取 $B=I_m$、$C=G_{11}$，因 $E_{11}G_{11}=E_{11}$，得
\[
T(E_{11})=T(E_{11}G_{11})=T(E_{11})G_{11},
\]
从而 $T(E_{11})$ 只有第 $1$ 列可能非零。
因此存在标量 $\lambda$ 使得
\[
T(E_{11})=\lambda E_{11}.
\]

再对任意 $p,q$，注意到
\[
F_{p1}E_{11}G_{1q}=E_{pq}.
\]
由双边条件
\[
T(E_{pq})=T(F_{p1}E_{11}G_{1q})=F_{p1}T(E_{11})G_{1q}
=F_{p1}(\lambda E_{11})G_{1q}=\lambda E_{pq}.
\]
由线性性，任意 $A=\sum_{p,q}a_{pq}E_{pq}$ 都满足
\[
T(A)=\sum_{p,q}a_{pq}T(E_{pq})=\lambda\sum_{p,q}a_{pq}E_{pq}=\lambda A.
\]
反之任取 $\lambda$ 定义 $T_\lambda(A)=\lambda A$ 显然满足双边条件。

故 $W=\{\lambda\,\mathrm{Id}\mid \lambda\in\Bbb F\}$，从而
\[
\dim W=1.
\]
\end{solution}
\begin{exercise}[变式2：右侧只要求对角矩阵（考察“按列分解”）]
设 $m,n,k$ 为正整数，令 $D_n\subset M_{n\times n}$ 为所有对角矩阵构成的子代数。
设 $L:M_{m\times n}\to M_{k\times n}$ 为线性映射，记
\[
W=\{L\mid L(AD)=L(A)D,\ \forall\,A\in M_{m\times n},\ \forall\,D\in D_n\}.
\]
求 $\dim W$，并刻画所有这样的 $L$。
\end{exercise}
\begin{solution}
对每个 $j=1,\dots,n$，令 $P_j=\mathrm{diag}(0,\dots,0,1,0,\dots,0)\in D_n$（第 $j$ 个对角元为 $1$）。
则对任意 $A$，$AP_j$ 恰好“抽取”$A$ 的第 $j$ 列（其余列变为 $0$）。

由条件
\[
L(AP_j)=L(A)P_j.
\]
右边 $L(A)P_j$ 只有第 $j$ 列可能非零，因此 $L(AP_j)$ 也只能在第 $j$ 列非零：
这表明 $L$ 把“仅第 $j$ 列可能非零”的子空间
\[
U_j:=\{A\in M_{m\times n}\mid A\text{除第 }j\text{列外全为 }0\}
\]
映到
\[
V_j:=\{Y\in M_{k\times n}\mid Y\text{除第 }j\text{列外全为 }0\}.
\]
注意到 $M_{m\times n}=\bigoplus_{j=1}^n U_j$，$M_{k\times n}=\bigoplus_{j=1}^n V_j$，
故 $L$ 等价于给出 $n$ 个线性映射 $L_j:U_j\to V_j$ 的直和。

而 $U_j$ 与 $\Bbb F^m$ 同构（用“第 $j$ 列”识别），$V_j$ 与 $\Bbb F^k$ 同构（同理），
因此每个 $L_j$ 等价于任意线性映射
\[
T_j:\Bbb F^m\to \Bbb F^k.
\]
于是所有 $L\in W$ 恰好形如：对任意 $A=[\,a_1\ \cdots\ a_n\,]$（$a_j$ 为第 $j$ 列），
\[
L(A)=[\,T_1(a_1)\ \cdots\ T_n(a_n)\,],
\]
其中 $T_1,\dots,T_n$ 可任取线性映射。

因此
\[
\dim W=\sum_{j=1}^n \dim \mathrm{Hom}(\Bbb F^m,\Bbb F^k)
= n\cdot (mk)=nmk.
\]
\end{solution}

\begin{exercise}[变式3（稍难：右作用由幂零 Jordan 块生成）]
设 $\mathbb F$ 为一域，$m,n,k$ 为正整数。令 $J\in M_{n\times n}(\mathbb F)$ 为幂零 Jordan 块
\[
J=\begin{pmatrix}
0&1&0&\cdots&0\\
0&0&1&\ddots&\vdots\\
\vdots&\ddots&\ddots&\ddots&0\\
0&\cdots&0&0&1\\
0&\cdots&\cdots&0&0
\end{pmatrix}.
\]
设 $L:M_{m\times n}(\mathbb F)\to M_{k\times n}(\mathbb F)$ 为线性映射，满足
\[
L(AJ)=L(A)J,\qquad \forall\,A\in M_{m\times n}(\mathbb F).
\]
令
\[
W=\{L\mid L(AJ)=L(A)J,\ \forall A\}.
\]
\begin{enumerate}
\item 证明对任意多项式 $p(t)\in\mathbb F[t]$，都有 $L(Ap(J))=L(A)p(J)$；
\item 证明存在且仅存在 $C_0,\dots,C_{n-1}\in M_{k\times m}(\mathbb F)$ 使得
\[
L(A)=\sum_{r=0}^{n-1} C_r A J^r,\qquad \forall\,A\in M_{m\times n}(\mathbb F),
\]
并求 $\dim W$。
\end{enumerate}
\end{exercise}
\begin{solution}
\textbf{(1)} 由题设 $L(AJ)=L(A)J$，对 $r\ge 1$ 用归纳法：
若已知 $L(AJ^r)=L(A)J^r$，则
\[
L(AJ^{r+1})=L\bigl((AJ^r)J\bigr)=L(AJ^r)J=L(A)J^rJ=L(A)J^{r+1}.
\]
故对所有 $r\ge 0$ 都有 $L(AJ^r)=L(A)J^r$（$r=0$ 时显然）。
于是对任意多项式 $p(t)=\sum_{r=0}^d a_r t^r$，
\[
L(Ap(J))
=L\Bigl(A\sum_{r=0}^d a_r J^r\Bigr)
=\sum_{r=0}^d a_r L(AJ^r)
=\sum_{r=0}^d a_r L(A)J^r
=L(A)p(J).
\]

\textbf{(2)} 记 $E_{ij}\in M_{m\times n}(\mathbb F)$ 为矩阵单位。
注意 $E_{i1}J^{j-1}=E_{ij}$（$1\le j\le n$），因此 $\{E_{ij}\}$ 都可由 $\{E_{i1}\}$ 右乘 $J$ 的幂生成。

对每个 $i=1,\dots,m$，记
\[
Y_i:=L(E_{i1})\in M_{k\times n}(\mathbb F),
\]
并把 $Y_i$ 的第 $(r+1)$ 列记作向量 $y_{i,r}\in\mathbb F^{k}$（其中 $r=0,1,\dots,n-1$）。
据此定义矩阵 $C_r\in M_{k\times m}(\mathbb F)$：其第 $i$ 列等于 $y_{i,r}$，即
\[
C_r=\bigl[y_{1,r}\ \ y_{2,r}\ \ \cdots\ \ y_{m,r}\bigr],\qquad r=0,1,\dots,n-1.
\]
我们断言对所有 $A$ 有
\[
L(A)=\sum_{r=0}^{n-1} C_r A J^r.
\]

只需在基 $E_{ij}$ 上验证。由(1)知
\[
L(E_{ij})=L(E_{i1}J^{j-1})=L(E_{i1})J^{j-1}=Y_iJ^{j-1}.
\]
另一方面，
\[
\sum_{r=0}^{n-1} C_r E_{ij}J^r
=\sum_{r=0}^{n-1} C_r(E_{i1}J^{j-1})J^r
=\sum_{r=0}^{n-1} (C_rE_{i1})J^{j+r-1}.
\]
注意 $C_rE_{i1}$ 是一个 $k\times n$ 矩阵，只有第 $1$ 列可能非零，
且该列恰为 $C_r$ 的第 $i$ 列，即 $y_{i,r}$；
右乘 $J^{j+r-1}$ 会把这唯一的非零列从第 $1$ 列平移到第 $j+r$ 列（若 $j+r\le n$，否则变为 $0$）。
因此上述和的第 $j+r$ 列正是 $y_{i,r}$，这与 $Y_iJ^{j-1}$ 的列结构完全一致：
$Y_iJ^{j-1}$ 的第 $j+r$ 列恰为 $Y_i$ 的第 $r+1$ 列，即 $y_{i,r}$。
故对每个 $E_{ij}$，
\[
L(E_{ij})=\sum_{r=0}^{n-1} C_r E_{ij}J^r.
\]
线性性推出对任意 $A$ 也成立，从而得到了所需表示式（存在性）。

\emph{唯一性}：若也有 $L(A)=\sum_{r=0}^{n-1} \widetilde C_rAJ^r$，
对 $A=E_{i1}$ 得
\[
L(E_{i1})=\sum_{r=0}^{n-1}\widetilde C_rE_{i1}J^r.
\]
右边的第 $r+1$ 列正是 $\widetilde C_r$ 的第 $i$ 列；而左边第 $r+1$ 列是 $Y_i$ 的第 $r+1$ 列 $y_{i,r}$。
故对每个 $r$ 和 $i$，$\widetilde C_r$ 的第 $i$ 列都等于 $y_{i,r}$，
即 $\widetilde C_r=C_r$，唯一性成立。

最后，$(C_0,\dots,C_{n-1})\mapsto L$ 给出线性同构
\[
W\cong \bigl(M_{k\times m}(\mathbb F)\bigr)^{\oplus n},
\]
故
\[
\dim W=n\cdot \dim M_{k\times m}(\mathbb F)=nkm.
\]  
\end{solution}

\begin{problem}[第3题]
记
\[
V=\{f\in C^\infty(\mathbb{R})\mid f(x+1)=2f(x)\}.
\]
定义 $L:V\to V,\ L(f)=f'+k f$。  
求所有的实数 $k$，使得 $L$ 为双射。
\end{problem}
\begin{solution}
    令
\[
V=\{f\in C^\infty(\mathbb{R})\mid f(x+1)=2f(x)\},
\qquad
L(f)=f'+k f.
\]

先描述空间 $V$ 的结构。对 $f\in V$，令
\[
a=\ln 2,\qquad f(x)=e^{ax}h(x).
\]
则
\[
f(x+1)=2f(x)
\iff
e^{a(x+1)}h(x+1)=2e^{ax}h(x)
\iff
e^a h(x+1)=2h(x).
\]
由于 $e^a=2$，于是 $h(x+1)=h(x)$，即 $h$ 为 $1$-周期的光滑函数。于是
\[
V=\{\,e^{ax}h(x)\mid h\in C^\infty(\mathbb{R}),\ h(x+1)=h(x)\,\}.
\]
记
\[
\mathcal P:=\{h\in C^\infty(\mathbb{R})\mid h(x+1)=h(x)\},
\]
则 $V\cong\mathcal P$，同构由 $h\mapsto e^{ax}h$ 给出。

对 $f=e^{ax}h$，
\[
f' = ae^{ax}h + e^{ax}h' = e^{ax}(h'+ah),
\]
故
\[
L(f)=f'+kf=e^{ax}\bigl(h'+(a+k)h\bigr).
\]
由此可知，在上述同构下，$L$ 对应于算子
\[
M:\mathcal P\to\mathcal P,\qquad M(h)=h'+(a+k)h.
\]

接下来在 $\mathcal P$ 中用傅里叶展开研究 $M$ 的可逆性。任意 $1$-周期光滑函数可写为
\[
h(x)=\sum_{n\in\mathbb Z}c_n e^{2\pi i n x}.
\]
于是
\[
M(h)(x)
= \sum_{n\in\mathbb Z}c_n\bigl(2\pi i n + a + k\bigr)e^{2\pi i n x}.
\]
可见，每个基函数 $e^{2\pi i n x}$ 是 $M$ 的特征向量，对应特征值
\[
\lambda_n = 2\pi i n + a + k,\qquad n\in\mathbb Z.
\]

算子 $M$ 可逆，当且仅当对所有 $n\in\mathbb Z$，$\lambda_n\neq 0$。因为若所有 $\lambda_n\neq 0$，则对任意 $g(x)=\sum d_n e^{2\pi i n x}\in\mathcal P$，可取
\[
c_n=\frac{d_n}{\lambda_n},
\]
从而 $h=\sum c_n e^{2\pi i n x}$ 满足 $M(h)=g$，且 $h$ 唯一，故 $M$ 双射；反之若某个 $\lambda_n=0$，则存在非零 $h$ 在核中，故 $M$ 非单射，更非双射。

现讨论
\[
\lambda_n = 2\pi i n + a + k = 0
\]
的可能性。这里 $k\in\mathbb R$，$a=\ln 2\in\mathbb R$。若 $n\neq 0$，则 $2\pi i n$ 为纯虚数，$a+k$ 为实数，不可能相加为 $0$。故唯一可能是 $n=0$：
\[
\lambda_0 = a + k = \ln 2 + k.
\]
因此
\[
\exists\,n\in\mathbb Z\text{ 使 }\lambda_n=0
\iff \lambda_0=0
\iff k=-\ln 2.
\]

综上，$M$（从而 $L$）是双射当且仅当
\[
k\neq -\ln 2.
\]
\end{solution}

\begin{exercise}[变式1：一般平移-伸缩条件]
设 $a>0,\ T>0$，定义
\[
V_{a,T}=\{f\in C^\infty(\mathbb R)\mid f(x+T)=a\,f(x)\}.
\]
定义线性算子 $L:V_{a,T}\to V_{a,T}$，$L(f)=f'+k f$（$k\in\mathbb R$）。
求所有使得 $L$ 为双射的 $k$。
\end{exercise}
\begin{solution}
令
\[
g(x)=a^{-x/T}f(x)=e^{-(\ln a)x/T}f(x).
\]
则
\[
g(x+T)=a^{-(x+T)/T}f(x+T)=a^{-x/T-1}\cdot a f(x)=g(x),
\]
故 $g$ 是 $T$-周期的光滑函数。反之任给 $T$-周期 $g$，令 $f(x)=a^{x/T}g(x)$ 则 $f\in V_{a,T}$。
因此 $V_{a,T}\cong C^\infty_{\mathrm{per},T}$（$T$-周期光滑函数空间）。

把 $f=a^{x/T}g$ 代入：
\[
f'=a^{x/T}\Bigl(g'+\frac{\ln a}{T}g\Bigr),
\]
从而
\[
L(f)=f'+kf=a^{x/T}\Bigl(g'+\Bigl(k+\frac{\ln a}{T}\Bigr)g\Bigr).
\]
于是 $L$ 在 $V_{a,T}$ 上是否双射，等价于算子
\[
T_c:C^\infty_{\mathrm{per},T}\to C^\infty_{\mathrm{per},T},\qquad T_c(g)=g'+c g,
\quad c:=k+\frac{\ln a}{T}
\]
是否双射。

\textbf{(1) 若 $c=0$，}则 $T_0(g)=g'$。其核包含所有常数函数，非单射；且像为所有满足
$\int_0^T h(x)\,dx=0$ 的周期函数，非满射。故 $c=0$ 时 $L$ 非双射。

\textbf{(2) 若 $c\neq 0$，}先看单射：$g'+cg=0$ 的解为 $g(x)=Ce^{-cx}$。若 $g$ 为 $T$-周期，
则 $Ce^{-c(x+T)}=Ce^{-cx}$，即 $e^{-cT}=1$。在 $c\in\mathbb R$ 下只能推出 $c=0$，与假设矛盾，
故只能 $C=0$，从而核为 $\{0\}$。

再看满射：给定任意 $T$-周期 $h$，解 $g'+cg=h$。
由积分因子法，
\[
g(x)=e^{-cx}\Bigl(g(0)+\int_0^x e^{ct}h(t)\,dt\Bigr).
\]
施加周期条件 $g(T)=g(0)$ 得
\[
(e^{-cT}-1)g(0)=-e^{-cT}\int_0^T e^{ct}h(t)\,dt.
\]
因 $c\neq 0$ 故 $e^{-cT}\neq 1$，于是 $g(0)$ 唯一确定，进而得到唯一的 $T$-周期解 $g$。
故 $T_c$ 双射，从而 $L$ 双射。

综上，$L$ 为双射当且仅当 $c\neq 0$，即
\[
k\neq -\frac{\ln a}{T}.
\]
\end{solution}

\begin{exercise}[变式2：系数为周期函数（参数进入非平凡位置）]
仍取
\[
V=\{f\in C^\infty(\mathbb R)\mid f(x+1)=2f(x)\}.
\]
对 $k\in\mathbb R$ 定义算子 $L_k:V\to V$：
\[
L_k(f)=f'(x)+k\sin(2\pi x)\,f(x).
\]
求所有使得 $L_k$ 为双射的 $k$。
\end{exercise}
\begin{solution}
先验证 $L_k$ 的确映 $V$ 到 $V$：因 $\sin(2\pi(x+1))=\sin(2\pi x)$ 且由 $f(x+1)=2f(x)$ 可得
$f'(x+1)=2f'(x)$，于是
\[
L_k(f)(x+1)=f'(x+1)+k\sin(2\pi(x+1))f(x+1)=2f'(x)+2k\sin(2\pi x)f(x)=2L_k(f)(x).
\]

令
\[
g(x)=2^{-x}f(x),
\]
则 $g(x+1)=g(x)$，即 $g$ 为 $1$-周期光滑函数，且 $f(x)=2^x g(x)$。
计算
\[
f'=2^x(g'+(\ln2)g),
\]
从而
\[
L_k(f)=2^x\Bigl(g'+\bigl(\ln2+k\sin(2\pi x)\bigr)g\Bigr).
\]
因此 $L_k$ 在 $V$ 上的双射性等价于算子
\[
T_k:C^\infty_{\mathrm{per}}\to C^\infty_{\mathrm{per}},\qquad
T_k(g)=g'+a_k(x)g,\quad a_k(x)=\ln2+k\sin(2\pi x)
\]
在 $1$-周期光滑函数空间上的双射性。

\textbf{单射：}若 $T_k(g)=0$，则
\[
g(x)=C\exp\Bigl(-\int_0^x a_k(t)\,dt\Bigr).
\]
利用 $a_k$ 的周期性可得
\[
g(1)=g(0)\exp\Bigl(-\int_0^1 a_k(t)\,dt\Bigr).
\]
而
\[
\int_0^1 a_k(t)\,dt=\int_0^1\ln2\,dt+k\int_0^1\sin(2\pi t)\,dt=\ln2,
\]
故 $g(1)=g(0)e^{-\ln2}=g(0)/2$。若 $g$ 为 $1$-周期，则 $g(1)=g(0)$，只能 $g(0)=0$，
进而 $C=0$，故 $g\equiv 0$，单射成立。

\textbf{满射：}给定任意 $1$-周期光滑函数 $h$，求 $T_k(g)=h$：
\[
g'+a_k(x)g=h.
\]
取积分因子
\[
\mu(x)=\exp\Bigl(\int_0^x a_k(t)\,dt\Bigr),
\]
则 $(\mu g)'=\mu h$，从而
\[
g(x)=\mu(x)^{-1}\Bigl(C+\int_0^x \mu(t)h(t)\,dt\Bigr).
\]
周期条件 $g(1)=g(0)=C$ 给出
\[
C=\mu(1)^{-1}\Bigl(C+\int_0^1\mu(t)h(t)\,dt\Bigr)
\iff
C(\mu(1)-1)=\int_0^1\mu(t)h(t)\,dt.
\]
而
\[
\mu(1)=\exp\Bigl(\int_0^1 a_k(t)\,dt\Bigr)=e^{\ln2}=2\neq 1,
\]
故 $C$ 唯一确定，从而存在唯一 $1$-周期解 $g$。满射成立。

因此 $T_k$（从而 $L_k$）对所有 $k\in\mathbb R$ 都是双射。
\[
\boxed{\text{$L_k$ 对一切实数 $k$ 都为双射。}}
\]
\end{solution}

\begin{exercise}[变式3：二阶算子（出现离散禁值集合）]
仍取
\[
V=\{f\in C^\infty(\mathbb R)\mid f(x+1)=2f(x)\}.
\]
对 $k\in\mathbb R$ 定义
\[
L_k:V\to V,\qquad L_k(f)=f''-2(\ln2)\,f'+k f.
\]
求所有使得 $L_k$ 为双射的 $k$。
\end{exercise}
\begin{solution}
同理可验 $L_k$ 映 $V$ 到 $V$，因为 $f,f',f''$ 都满足同样的伸缩关系：
$f^{(r)}(x+1)=2f^{(r)}(x)$，故 $L_k(f)(x+1)=2L_k(f)(x)$。

仍令 $g(x)=2^{-x}f(x)$，则 $g$ 为 $1$-周期光滑函数，且 $f=2^x g$。
计算
\[
f'=2^x(g'+(\ln2)g),\qquad
f''=2^x\bigl(g''+2(\ln2)g'+(\ln2)^2 g\bigr).
\]
代入得
\[
\begin{aligned}
L_k(f)
&=2^x\bigl(g''+2(\ln2)g'+(\ln2)^2 g\bigr)
-2(\ln2)\cdot 2^x\bigl(g'+(\ln2)g\bigr)+k\cdot 2^x g\\
&=2^x\Bigl(g''+\bigl(k-(\ln2)^2\bigr)g\Bigr).
\end{aligned}
\]
因此 $L_k$ 在 $V$ 上双射 $\Longleftrightarrow$
\[
S_\alpha:C^\infty_{\mathrm{per}}\to C^\infty_{\mathrm{per}},\qquad S_\alpha(g)=g''+\alpha g,
\quad \alpha:=k-(\ln2)^2
\]
在 $1$-周期光滑函数空间上双射。

对 $g$ 作傅里叶展开
\[
g(x)=\sum_{n\in\mathbb Z} a_n e^{2\pi i n x},
\]
则
\[
g''(x)=\sum_{n\in\mathbb Z}(2\pi i n)^2 a_n e^{2\pi i n x}
=\sum_{n\in\mathbb Z}(-4\pi^2 n^2)a_n e^{2\pi i n x},
\]
从而
\[
S_\alpha(g)=\sum_{n\in\mathbb Z}(\alpha-4\pi^2 n^2)a_n e^{2\pi i n x}.
\]

\textbf{必要性：}若存在 $n_0\in\mathbb Z$ 使 $\alpha-4\pi^2 n_0^2=0$，
则 $e^{2\pi i n_0 x}\neq 0$ 且 $S_\alpha(e^{2\pi i n_0 x})=0$，核非零，故非单射，从而非双射。

\textbf{充分性：}若对所有 $n\in\mathbb Z$ 都有 $\alpha-4\pi^2 n^2\neq 0$，
给定任意 $h(x)=\sum b_n e^{2\pi i n x}\in C^\infty_{\mathrm{per}}$，
令
\[
a_n=\frac{b_n}{\alpha-4\pi^2 n^2},
\]
则形式上 $g(x)=\sum a_n e^{2\pi i n x}$ 满足 $S_\alpha(g)=h$。
由于 $h$ 光滑意味着 $b_n$ 比任意幂次都衰减，而分母增长为 $n^2$ 量级，
故 $a_n$ 仍比任意幂次衰减，$g$ 仍为光滑周期函数。解也唯一（逐系数唯一），
因此 $S_\alpha$ 双射。

综上，$S_\alpha$（从而 $L_k$）双射当且仅当
\[
\alpha\neq 4\pi^2 n^2\quad(\forall n\in\mathbb Z),
\]
即
\[
\boxed{\ k\neq (\ln2)^2+4\pi^2 n^2\quad(\forall n\in\mathbb Z)\ }.
\]
\end{solution}





\begin{problem}[第4题]
对于 $v=(v_1,v_2,\ldots,v_n),\ w=(w_1,w_2,\ldots,w_n)\in\mathbb{Q}^n$，定义内积
\[
\langle v,w\rangle=v_1w_1+v_2w_2+\cdots+v_nw_n.
\]
称 $\sigma$ 是正交变换，如果对任意 $v,w$，均有 $\langle \sigma(v),\sigma(w)\rangle=\langle v,w\rangle$。  
求证：对任意正交变换 $\sigma$，存在正交变换 $\tau_1,\tau_2,\ldots,\tau_k$，使得 $\sigma=\tau_1\tau_2\cdots\tau_k$，其中 $\{v\in V\mid \tau_i(v)=v\}$ 的维数为 $n-1$。
\end{problem}


\begin{solution}
    在 $\mathbb Q^n$ 上内积为
\[
\langle v,w\rangle = \sum_{i=1}^n v_i w_i.
\]
称线性变换 $\sigma:\mathbb Q^n\to\mathbb Q^n$ 为正交变换，若对任意 $v,w$ 有
\[
\langle \sigma(v),\sigma(w)\rangle = \langle v,w\rangle.
\]

\medskip

\textbf{一步：定义“反射”并验证其性质。}

对任意非零向量 $u\in\mathbb Q^n$，定义线性变换
\[
\tau_u(v) = v - 2\frac{\langle v,u\rangle}{\langle u,u\rangle}u.
\]
则
\[
\langle \tau_u(v),\tau_u(w)\rangle
= \langle v,w\rangle
\]
（可通过直接展开并利用双线性与对称性验证），故 $\tau_u$ 为正交变换。

又
\[
\tau_u(v)=v
\iff 2\frac{\langle v,u\rangle}{\langle u,u\rangle}u=0
\iff \langle v,u\rangle=0.
\]
故
\[
\{v\in\mathbb Q^n\mid \tau_u(v)=v\} = u^\perp := \{v\mid \langle v,u\rangle=0\},
\]
这是非零线性泛函 $v\mapsto\langle v,u\rangle$ 的核，维数为 $n-1$。此类 $\tau_u$ 就是题目中要求的那类正交变换（固定子空间维数为 $n-1$），通常称为“关于过原点超平面的反射”。

\medskip

\textbf{二步：用归纳证明任意正交变换可由若干反射之积表示。}

命题：对任意 $n\ge1$，任意正交变换 $\sigma:\mathbb Q^n\to\mathbb Q^n$，存在若干反射 $\tau_{u_1},\dots,\tau_{u_k}$（$k\le n$），使得
\[
\sigma = \tau_{u_1}\tau_{u_2}\cdots\tau_{u_k}.
\]

\emph{基情形 $n=1$}：此时 $\mathbb Q^1\cong\mathbb Q$。保内积的线性变换只有
\[
v\mapsto v\quad\text{和}\quad v\mapsto -v.
\]
恒等变换是 $0$ 个反射的乘积；而 $v\mapsto -v$ 即为关于 $u=1$ 的反射
\[
\tau_1(v)=v-2\frac{\langle v,1\rangle}{\langle1,1\rangle}1=v-2v=-v,
\]
其不动子空间为 $\{0\}$，维数 $1-1=0$。基情形成立。

\medskip

\emph{归纳假设}：假设命题对维数 $n-1$ 已成立。

\medskip

\emph{归纳步}：现设 $\sigma:\mathbb Q^n\to\mathbb Q^n$ 为正交变换。

\medskip

\underline{情况一：$\sigma$ 有非零不动向量。}

设存在 $0\ne v\in\mathbb Q^n$ 使得 $\sigma(v)=v$。令
\[
W = v^\perp = \{x\in\mathbb Q^n\mid \langle x,v\rangle=0\},
\]
则 $\dim W=n-1$。对 $w\in W$，
\[
\langle \sigma(w),v\rangle
= \langle \sigma(w),\sigma(v)\rangle
= \langle w,v\rangle
= 0,
\]
故 $\sigma(W)\subset W$，从而 $\sigma$ 的限制
\[
\sigma|_W:W\to W
\]
是 $W$ 上的正交变换。

根据归纳假设，存在 $W$ 内的若干反射
\[
\tilde\tau_1,\dots,\tilde\tau_k:W\to W
\]
使得
\[
\sigma|_W = \tilde\tau_1\cdots\tilde\tau_k.
\]
每个 $\tilde\tau_i$ 固定 $W$ 中某个维数为 $n-2$ 的子空间。现把它们延拓为 $\mathbb Q^n$ 上的正交变换：对 $x=\alpha v + w$（$w\in W$），定义
\[
\tau_i(x) := \alpha v + \tilde\tau_i(w).
\]
显然 $\tau_i$ 线性、正交（因为在分解 $\mathbb Q^n=\mathbb Q v\oplus W$ 上分别保内积），并且 $\tau_i(v)=v$，在 $W$ 上与 $\tilde\tau_i$ 一致。$\tau_i$ 的不动子空间为
\[
\mathbb Q v \oplus \{w\in W\mid \tilde\tau_i(w)=w\},
\]
维数为 $1+(n-2)=n-1$，符合要求。

对任意 $w\in W$，
\[
(\tau_1\cdots\tau_k)(w)=(\tilde\tau_1\cdots\tilde\tau_k)(w)=\sigma(w),
\]
且 $(\tau_1\cdots\tau_k)(v)=v=\sigma(v)$，于是对任意 $x=\alpha v+w\in\mathbb Q^n$，
\[
(\tau_1\cdots\tau_k)(x)=\alpha v+(\tau_1\cdots\tau_k)(w)
= \alpha v + \sigma(w)=\sigma(\alpha v + w)=\sigma(x).
\]
故
\[
\sigma = \tau_1\cdots\tau_k,
\]
写成了所需形式。

\medskip

\underline{情况二：$\sigma$ 无非零不动向量。}

取任意 $0\ne v\in\mathbb Q^n$，则 $\sigma(v)\ne v$。令
\[
u := v - \sigma(v).
\]
若 $\langle u,u\rangle=0$，则
\[
0=\langle u,u\rangle
= \langle v-\sigma(v),v-\sigma(v)\rangle
= \langle v,v\rangle -2\langle v,\sigma(v)\rangle + \langle\sigma(v),\sigma(v)\rangle.
\]
利用正交性 $\langle\sigma(v),\sigma(v)\rangle=\langle v,v\rangle$，得到
\[
0 = 2\bigl(\langle v,v\rangle - \langle v,\sigma(v)\rangle\bigr),
\]
即
\[
\langle v,\sigma(v)\rangle = \langle v,v\rangle.
\]
另一方面，由柯西–施瓦茨不等式（在 $\mathbb Q^n$ 同样成立）有
\[
|\langle v,\sigma(v)\rangle|\le 
\sqrt{\langle v,v\rangle}\sqrt{\langle\sigma(v),\sigma(v)\rangle}
= \langle v,v\rangle,
\]
等号成立当且仅当 $v$ 与 $\sigma(v)$ 线性相关且同向。此时
\[
\sigma(v)=\lambda v,\quad \lambda\in\mathbb Q,
\]
再由
\[
\langle\sigma(v),\sigma(v)\rangle = \langle v,v\rangle
\]
得 $\lambda^2=1$，又由 $\langle v,\sigma(v)\rangle=\langle v,v\rangle$ 得 $\lambda=1$，即 $\sigma(v)=v$，与“无非零不动向量”矛盾。故必有
\[
\langle u,u\rangle\ne 0.
\]

于是可定义关于 $u$ 的反射
\[
\tau_u(x)=x-2\frac{\langle x,u\rangle}{\langle u,u\rangle}u.
\]
它是正交变换，且不动子空间维数为 $n-1$。

计算
\[
\langle\sigma(v),u\rangle
= \langle\sigma(v),v-\sigma(v)\rangle
= \langle\sigma(v),v\rangle - \langle\sigma(v),\sigma(v)\rangle
= \langle v,\sigma(v)\rangle - \langle v,v\rangle.
\]
而
\[
\langle u,u\rangle
= \langle v-\sigma(v),v-\sigma(v)\rangle
= 2\bigl(\langle v,v\rangle - \langle v,\sigma(v)\rangle\bigr),
\]
从而
\[
\frac{\langle\sigma(v),u\rangle}{\langle u,u\rangle} = -\frac12.
\]
于是
\[
\tau_u(\sigma(v))
= \sigma(v) - 2\frac{\langle\sigma(v),u\rangle}{\langle u,u\rangle}u
= \sigma(v) - 2\cdot\Bigl(-\frac12\Bigr)u
= \sigma(v) + u
= \sigma(v) + (v-\sigma(v))
= v.
\]
即
\[
(\tau_u\circ\sigma)(v)=v,
\]
所以 $\tau_u\circ\sigma$ 在 $v$ 方向上有非零不动向量。

由情况一，$\tau_u\circ\sigma$ 可写成若干反射之积：
\[
\tau_u\circ\sigma = \tau_{u_1}\cdots\tau_{u_m}.
\]
于是
\[
\sigma = \tau_u^{-1}\tau_{u_1}\cdots\tau_{u_m}.
\]
但 $\tau_u$ 为反射，满足 $\tau_u^{-1}=\tau_u$，故
\[
\sigma = \tau_u\tau_{u_1}\cdots\tau_{u_m},
\]
即 $\sigma$ 是若干反射的乘积，每个反射的固定子空间维数都为 $n-1$。

\medskip

综上，对任意正交变换 $\sigma:\mathbb Q^n\to\mathbb Q^n$，都存在正交变换
\[
\tau_1,\dots,\tau_k\quad(\text{均为反射，}\dim\{v\mid\tau_i(v)=v\}=n-1)
\]
使得
\[
\sigma = \tau_1\tau_2\cdots\tau_k.
\]
证明完毕。$\square$
\end{solution}
\begin{exercise}[变式1：更换内积（加权有理内积）]
取 $d_1,\dots,d_n\in\mathbb Q_{>0}$，在 $\mathbb Q^n$ 上定义内积
\[
\langle v,w\rangle_D=\sum_{i=1}^n d_i v_i w_i.
\]
称线性同构 $\sigma:\mathbb Q^n\to\mathbb Q^n$ 为 $D$-正交变换，若对任意 $v,w$ 有
$\langle \sigma(v),\sigma(w)\rangle_D=\langle v,w\rangle_D$。
证明：任意 $D$-正交变换 $\sigma$ 都可写成若干个 $D$-正交变换的乘积
\[
\sigma=\tau_1\tau_2\cdots\tau_k,
\]
其中每个 $\tau_i$ 的不动子空间 $\{v\mid \tau_i(v)=v\}$ 的维数均为 $n-1$（即 $\tau_i$ 为关于某个超平面的“反射”）。
并给出每个 $\tau_i$ 的显式形式（用 $D$-内积表示）。
\end{exercise}
\begin{solution}
\textbf{(1) 反射的显式形式。}
对任意非零向量 $a\in\mathbb Q^n$，定义
\[
R_a(v)=v-\frac{2\langle v,a\rangle_D}{\langle a,a\rangle_D}\,a.
\]
由于 $d_i\in\mathbb Q$ 且 $a\in\mathbb Q^n$，故 $\langle a,a\rangle_D\in\mathbb Q_{>0}$，从而 $R_a$ 的系数均为有理数，
确实是 $\mathbb Q$-线性变换。

把 $v$ 分解为 $v=v_\perp+\lambda a$，其中 $v_\perp\in a^{\perp_D}:=\{x\mid \langle x,a\rangle_D=0\}$，
则由定义直接算得 $R_a(v)=v_\perp-\lambda a$。
因此 $R_a$ 在 $a^{\perp_D}$ 上恒等，在 $\mathbb Q a$ 上取相反数，故
\[
\langle R_a(v),R_a(w)\rangle_D=\langle v,w\rangle_D,
\]
即 $R_a$ 为 $D$-正交变换，且
\[
\mathrm{Fix}(R_a)=a^{\perp_D},\qquad \dim \mathrm{Fix}(R_a)=n-1.
\]

\textbf{(2) 分解（归纳构造）。}
令 $e_1,\dots,e_n$ 为标准基。注意在 $\langle\cdot,\cdot\rangle_D$ 下它们两两正交：
$\langle e_i,e_j\rangle_D=0\ (i\neq j)$。

用归纳法构造反射，使得逐步“固定”$e_1,e_2,\dots$。
设已构造出 $D$-正交变换 $\rho_{i-1}$ 使得
\[
\rho_{i-1}\sigma(e_j)=e_j,\qquad j=1,\dots,i-1.
\]
令 $\sigma_{i-1}:=\rho_{i-1}\sigma$，则 $\sigma_{i-1}$ 为 $D$-正交且固定 $e_1,\dots,e_{i-1}$。

考虑 $e_i$。若 $\sigma_{i-1}(e_i)=e_i$，令 $\rho_i=\rho_{i-1}$。
否则令
\[
a_i:=\sigma_{i-1}(e_i)-e_i\neq 0,\qquad \tau_i:=R_{a_i}.
\]
由于对 $j<i$，
\[
\langle a_i,e_j\rangle_D=\langle \sigma_{i-1}(e_i),e_j\rangle_D-\langle e_i,e_j\rangle_D
=\langle e_i,\sigma_{i-1}^{-1}(e_j)\rangle_D-0
=\langle e_i,e_j\rangle_D=0,
\]
故 $e_j\in a_i^{\perp_D}$，从而 $\tau_i(e_j)=e_j$（$j<i$）。

同时可直接验证 $\tau_i$ 把 $\sigma_{i-1}(e_i)$ 送到 $e_i$：
\[
\tau_i(\sigma_{i-1}(e_i))
=\sigma_{i-1}(e_i)-\frac{2\langle \sigma_{i-1}(e_i),a_i\rangle_D}{\langle a_i,a_i\rangle_D}a_i.
\]
而
\[
\langle \sigma_{i-1}(e_i),a_i\rangle_D
=\langle \sigma_{i-1}(e_i),\sigma_{i-1}(e_i)-e_i\rangle_D
=\langle e_i,e_i\rangle_D-\langle \sigma_{i-1}(e_i),e_i\rangle_D,
\]
且
\[
\langle a_i,a_i\rangle_D
=\langle \sigma_{i-1}(e_i)-e_i,\sigma_{i-1}(e_i)-e_i\rangle_D
=2\bigl(\langle e_i,e_i\rangle_D-\langle \sigma_{i-1}(e_i),e_i\rangle_D\bigr),
\]
故分式恰为 $1$，从而 $\tau_i(\sigma_{i-1}(e_i))=\sigma_{i-1}(e_i)-a_i=e_i$。

令 $\rho_i:=\tau_i\rho_{i-1}$，则 $\sigma_i:=\rho_i\sigma=\tau_i\sigma_{i-1}$ 固定 $e_1,\dots,e_i$。

如此迭代至 $i=n$，得到 $\rho_n\sigma=\mathrm{Id}$，即
\[
\sigma=\rho_n^{-1}=\tau_1\tau_2\cdots\tau_k
\]
（其中去掉那些等于恒等的 $\tau_i$ 即得）。每个 $\tau_i$ 都是某个 $R_{a_i}$，其不动空间维数为 $n-1$。
\end{solution}
\begin{exercise}[变式2：最少需要多少个反射？（反射长度 $=$ 秩）]
在 $\mathbb Q^n$ 上取标准内积 $\langle v,w\rangle=\sum_{i=1}^n v_i w_i$。
称 $\tau$ 为\emph{反射}，若 $\tau$ 为正交变换且 $\dim\mathrm{Fix}(\tau)=n-1$。
对任意正交变换 $\sigma$，定义它的\emph{反射长度}
\[
\ell(\sigma)=\min\bigl\{k:\ \sigma=\tau_1\cdots\tau_k,\ \tau_i\text{ 为反射}\bigr\}.
\]
证明：
\[
\ell(\sigma)=\mathrm{rank}(\sigma-I)=n-\dim\mathrm{Fix}(\sigma).
\]
并推出：$\det\sigma=(-1)^{\ell(\sigma)}$。    
\end{exercise}
\begin{solution}
\textbf{(1) 反射的秩与行列式。}
若 $\tau$ 为反射，则其特征值为 $1$（重数 $n-1$）与 $-1$（重数 $1$），因此
\[
\mathrm{rank}(\tau-I)=1,\qquad \det\tau=-1.
\]

\textbf{(2) 一个秩不等式。}
对任意 $n\times n$ 矩阵 $A,B$，有
\[
\mathrm{rank}(AB-I)\le \mathrm{rank}(A-I)+\mathrm{rank}(B-I).
\]
证明：注意
\[
AB-I=A(B-I)+(A-I),
\]
因此
\[
\mathrm{Im}(AB-I)\subseteq \mathrm{Im}(A(B-I))+\mathrm{Im}(A-I).
\]
又 $\mathrm{rank}(A(B-I))\le \mathrm{rank}(B-I)$，故结论成立。

\textbf{(3) 下界：$\ell(\sigma)\ge \mathrm{rank}(\sigma-I)$。}
若 $\sigma=\tau_1\cdots\tau_k$，反复用(2)得
\[
\mathrm{rank}(\sigma-I)\le \sum_{i=1}^k \mathrm{rank}(\tau_i-I)=k,
\]
于是 $\ell(\sigma)\ge \mathrm{rank}(\sigma-I)$。

\textbf{(4) 上界：$\ell(\sigma)\le \mathrm{rank}(\sigma-I)$。}
令 $r=\mathrm{rank}(\sigma-I)$，并设
\[
U:=\mathrm{Im}(\sigma-I).
\]
对任意 $y\in\mathrm{Fix}(\sigma)$ 与任意 $x$，有
\[
\langle (\sigma-I)x,y\rangle=\langle \sigma x,y\rangle-\langle x,y\rangle
=\langle x,\sigma^{-1}y\rangle-\langle x,y\rangle=0,
\]
故 $U\subseteq \mathrm{Fix}(\sigma)^\perp$。比较维数：
\[
\dim U=r,\qquad \dim \mathrm{Fix}(\sigma)^\perp=n-\dim\mathrm{Fix}(\sigma)=\mathrm{rank}(\sigma-I)=r,
\]
所以
\[
U=\mathrm{Fix}(\sigma)^\perp.
\]
从而 $\mathbb Q^n=\mathrm{Fix}(\sigma)\oplus U$ 为正交直和，且 $U$ 在 $\sigma$ 下不变。

把 $\sigma$ 限制到 $U$ 上，记 $\sigma_U:=\sigma|_U$。若 $u\in U$ 且 $\sigma_U(u)=u$，
则 $u\in U\cap \mathrm{Fix}(\sigma)=\{0\}$，故 $\sigma_U$ 在 $U$ 上没有非零不动向量。

现在在维数为 $r$ 的内积空间 $U$ 上用归纳法证明：$\sigma_U$ 可写为 $r$ 个（至多 $r$ 个）反射的乘积。
当 $r=0$ 时平凡；当 $r\ge1$ 时，取任意 $0\neq v\in U$，则 $\sigma_U(v)\neq v$，令
\[
a:=\sigma_U(v)-v\neq 0,\qquad \rho:=R_a,\quad R_a(x)=x-\frac{2\langle x,a\rangle}{\langle a,a\rangle}a.
\]
则同样计算可得 $\rho(\sigma_U(v))=v$，因此 $\rho\sigma_U$ 固定 $v$。
于是 $\rho\sigma_U$ 保持 $v^\perp\cap U$（维数 $r-1$）并在其上仍为正交变换，
对该子空间应用归纳假设即可把 $\rho\sigma_U$ 分解为至多 $r-1$ 个反射，
从而 $\sigma_U$ 分解为至多 $r$ 个反射。

将这些 $U$ 上的反射按“在 $\mathrm{Fix}(\sigma)$ 上取恒等、在 $U$ 上按原反射作用”的方式延拓到 $\mathbb Q^n$，
每个延拓后的变换仍然是 $\mathbb Q^n$ 上的反射（其不动空间维数为 $(n-r)+(r-1)=n-1$），
并且它们的乘积在 $\mathrm{Fix}(\sigma)$ 上为恒等、在 $U$ 上等于 $\sigma_U$，即整体等于 $\sigma$。
故 $\ell(\sigma)\le r$。

结合(3)与(4)，得
\[
\ell(\sigma)=\mathrm{rank}(\sigma-I)=n-\dim\mathrm{Fix}(\sigma).
\]

\textbf{(5) 行列式结论。}
由(1)知每个反射行列式为 $-1$，故任意分解 $\sigma=\tau_1\cdots\tau_{\ell(\sigma)}$ 给出
\[
\det\sigma=\prod_{i=1}^{\ell(\sigma)}\det\tau_i=(-1)^{\ell(\sigma)}.
\]
\end{solution}
\begin{exercise}[变式3：整数正交矩阵的结构与反射分解]
设 $\sigma\in M_{n\times n}(\mathbb Z)$ 满足 $\sigma^T\sigma=I_n$（即 $\sigma$ 为整数正交矩阵）。
\begin{enumerate}
\item 证明：$\sigma$ 必为\emph{符号置换矩阵}（每一行每一列恰有一个非零元，且该非零元为 $\pm1$）。
\item 证明：任意这样的 $\sigma$ 都可写成若干个反射的乘积，其中每个反射 $\tau$ 满足
$\tau\in M_{n\times n}(\mathbb Z)$ 且 $\dim\mathrm{Fix}(\tau)=n-1$。
并给出一个只依赖于 $n$ 的反射个数上界。
\end{enumerate}
\end{exercise}
\begin{solution}
\textbf{(1) 结构刻画。}
记 $\sigma=[c_1\ \cdots\ c_n]$（按列分块），则 $\sigma^T\sigma=I$ 等价于
\[
\langle c_i,c_j\rangle=\delta_{ij}.
\]
特别地，每个 $c_i\in\mathbb Z^n$ 且 $\langle c_i,c_i\rangle=1$。
但整数向量平方和为 $1$ 只能意味着 $c_i$ 恰有一个分量为 $\pm1$，其余为 $0$。
又 $\langle c_i,c_j\rangle=0\ (i\neq j)$ 迫使这些 $\pm1$ 出现在互不相同的位置。
因此 $\sigma$ 的每一列都是某个 $\pm e_{p(i)}$，从而 $\sigma$ 为符号置换矩阵。

\textbf{(2) 分解为整数反射。}
任意符号置换矩阵可写为
\[
\sigma=DP,
\]
其中 $D=\mathrm{diag}(\varepsilon_1,\dots,\varepsilon_n)$，$\varepsilon_i\in\{\pm1\}$，
$P$ 为置换矩阵。

\emph{(a) 对角符号部分 $D$：}
当 $\varepsilon_i=-1$ 时，令
\[
s_i=\mathrm{diag}(1,\dots,1,\underbrace{-1}_{i\text{ 处}},1,\dots,1),
\]
则 $s_i$ 为反射（固定超平面 $\{x_i=0\}$，维数 $n-1$），且 $D=\prod_{\varepsilon_i=-1}s_i$。
所需反射个数不超过 $n$。

\emph{(b) 置换部分 $P$：}
任意置换可写成若干个对换（转置）的乘积，且最多用 $n-1$ 个对换即可（例如把每个循环分解为对换）。
对换 $(i\ j)$ 对应的置换矩阵 $T_{ij}$ 交换第 $i,j$ 个坐标。
令 $u=e_i-e_j$，定义反射
\[
r_{ij}(x)=x-\frac{2\langle x,u\rangle}{\langle u,u\rangle}u
=x-\langle x,e_i-e_j\rangle\,(e_i-e_j),
\]
因为 $\langle u,u\rangle=2$。
直接计算得 $r_{ij}$ 交换第 $i,j$ 坐标且固定其余坐标，因此 $r_{ij}=T_{ij}$。
于是 $P$ 可写为至多 $n-1$ 个反射的乘积。

综上，$\sigma=DP$ 可写为至多
\[
n+(n-1)=2n-1
\]
个整数反射（均满足不动空间维数为 $n-1$）的乘积。   
\end{solution}


\begin{problem}[第5题]
设 $A$ 为 $n$ 阶置换群 $S_n$ 的 Abel 子群，且对任意 $1\le k\le n$，存在 $\sigma\in A$，使得 $\sigma(1)=k$。  
(1) 求 $A$ 的阶；  
(2) 求所有的正整数 $n$，使得 $A$ 在同构意义下唯一。
\end{problem}
\begin{solution}
(1) 记
\[
A_1=\{\sigma\in A\mid \sigma(1)=1\}
\]
为 $A$ 在点 $1$ 的稳定子．由轨道–稳定子定理，
\[
|\operatorname{Orb}_A(1)|=\frac{|A|}{|A_1|}.
\]
题设给出：对任意 $1\le k\le n$，存在 $\sigma\in A$ 使得 $\sigma(1)=k$，故
\[
\operatorname{Orb}_A(1)=\{1,2,\dots,n\},
\]
从而 $|\operatorname{Orb}_A(1)|=n$，即
\[
\frac{|A|}{|A_1|}=n.
\]

另一方面，对任意 $i\in\{1,\dots,n\}$，取 $\tau\in A$ 使得 $\tau(1)=i$，则
\[
A_i=\{\sigma\in A\mid \sigma(i)=i\}
  =\{\sigma\in A\mid \tau^{-1}\sigma\tau(1)=1\}
  =\tau A_1\tau^{-1}.
\]
但 $A$ 为 Abel 群，故共轭作用是平凡的，$\tau A_1\tau^{-1}=A_1$，于是
\[
A_i=A_1\quad\text{对一切 }i.
\]
从而 $A_1$ 中的每个元素固定每一个 $i$，故 $A_1$ 中任意元素都为恒等置换，于是
\[
A_1=\{1\}.
\]
代回轨道–稳定子公式得
\[
|A|=n.
\]

(2) 由 (1) 知，满足条件的 $A$ 必为 $n$ 阶 Abel 群，并在 $n$ 个点上作正则（即传递且稳定子平凡）的置换作用．反过来，任取一个 $n$ 阶 Abel 群 $G$，把点集 $\{1,\dots,n\}$ 与 $G$ 的底集作一个双射识别，则左平移作用
\[
G\curvearrowright G,\quad g:h\mapsto gh
\]
给出一个单射同态 $G\hookrightarrow S_n$，像是一个 $n$ 阶的 Abel 子群，在该作用下每个点的轨道为全体点，且稳定子平凡，因此必满足题设条件．

于是：在同构意义下，题中所有可能的 $A$ 与“所有 $n$ 阶 Abel 群”一一对应．因此 $A$ 在同构意义下唯一，当且仅当 \emph{所有 $n$ 阶 Abel 群同构}．

由有限 Abel 群基本定理，若
\[
n=\prod_{i=1}^r p_i^{\alpha_i}
\]
是素因子分解，则 $n$ 阶 Abel 群的同构类型个数为
\[
\prod_{i=1}^r P(\alpha_i),
\]
其中 $P(k)$ 为 $k$ 的分拆数．当且仅当每个 $\alpha_i=1$ 时，$P(\alpha_i)=1$，否则若某个 $\alpha_i\ge2$，则 $P(\alpha_i)\ge2$，会出现多个不同的 Abel 群同构类型（例如 $p^2$ 阶时有 $\mathbb Z_{p^2}$ 与 $\mathbb Z_p\times\mathbb Z_p$ 两种）．

故 $A$ 在同构意义下唯一 $\Longleftrightarrow$ 对所有 $i$，$\alpha_i\le1$，即 $n$ 为无平方因子整数（平方自由数）．

\medskip
综上：
\[
(1)\ |A|=n;\qquad
(2)\ A\text{ 在同构意义下唯一当且仅当 }n\text{ 为平方自由数}.
\]
\end{solution}

\begin{exercise}[变式1：加入“是否含 $n$-循环”的结构判别]
设 $A\le S_n$ 为 Abel 子群，并且 $A$ 在 $\{1,2,\dots,n\}$ 上传递：对任意 $i,j$，存在 $\sigma\in A$ 使得 $\sigma(i)=j$。
\begin{enumerate}
\item 证明 $|A|=n$（并说明该作用是正则的：除恒等元外无元素有不动点）。
\item 证明：$A$ 含有一个 $n$-循环当且仅当 $A$ 是循环群。
\item 求所有正整数 $n$，使得任意满足条件的 $A$ 都必为循环群（等价地：必含 $n$-循环）。
\end{enumerate}
\end{exercise}
\begin{solution}
\textbf{(1)} 设 $H=\mathrm{Stab}_A(1)=\{\sigma\in A\mid \sigma(1)=1\}$。
任取 $\tau\in H$，任取 $k\in\{1,\dots,n\}$，由传递性可取 $\gamma\in A$ 使得 $\gamma(1)=k$。
因 $A$ 交换，$\gamma\tau\gamma^{-1}=\tau$，故
\[
\tau(k)=\tau(\gamma(1))=\gamma\tau(1)=\gamma(1)=k.
\]
于是 $\tau$ 固定所有点，故 $\tau=\mathrm{id}$，从而 $H=\{1\}$。
由轨道-稳定子定理，
\[
|A|=|\mathrm{Orb}_A(1)|\cdot|\mathrm{Stab}_A(1)|=n\cdot 1=n.
\]
同理任意点稳定子皆为平凡群，因此除恒等元外没有元素固定任何点（作用正则）。

\textbf{(2)} 若 $A$ 含 $n$-循环 $\pi$，则 $\langle \pi\rangle$ 的阶为 $n$。
而由(1)知 $|A|=n$，故 $A=\langle \pi\rangle$ 为循环群。

反之若 $A$ 为循环群且 $|A|=n$，则 $A=\langle \pi\rangle$，其中 $\pi$ 的阶为 $n$。
由(1)知作用正则，因此 $\pi$ 的每个轨道大小都等于 $\mathrm{ord}(\pi)=n$，即 $\pi$ 恰是一条 $n$-循环。

\textbf{(3)} “任意满足条件的 $A$ 都必循环”等价于“任意阶为 $n$ 的 Abel 群都必循环”，
也等价于“阶为 $n$ 的 Abel 群在同构意义下唯一”。

设 $n=\prod_{i=1}^r p_i^{a_i}$ 为素因子分解。有限 Abel 群结构定理给出：阶为 $n$ 的 Abel 群同构类型由各个
$p_i$-主分量的分解决定，而阶为 $p^a$ 的 Abel 群当 $a\ge2$ 时至少有两个不同同构类型
（例如 $C_{p^a}$ 与 $C_{p^{a-1}}\times C_p$）。
因此要使同构类型唯一，必须且只需对每个 $i$ 有 $a_i=1$，即 $n$ 平方因子自由（squarefree）。

故答案为：当且仅当 $n$ 为平方因子自由整数时，任意这样的 $A$ 必为循环群（从而必含 $n$-循环）。
\end{solution}
\begin{exercise}[变式2：加入“指数为素数”的限制（迫使为初等 Abel 群）]
设 $p$ 为素数，$A\le S_n$ 为 Abel 子群，并满足：对任意 $1\le k\le n$，存在 $\sigma\in A$ 使得 $\sigma(1)=k$。
另外假设 $A$ 中任一非恒等元素的阶都等于 $p$（即 $A$ 的指数为 $p$）。
\begin{enumerate}
\item 求 $n$ 与 $|A|$；
\item 在同构意义下刻画所有可能的 $A$；
\item 给出一种显式构造把这样的 $A$ 嵌入到 $S_n$ 中。
\end{enumerate}
\end{exercise}
\begin{solution}
\textbf{(1)} 与原题同样，设 $H=\mathrm{Stab}_A(1)$。
若 $\tau\in H$，对任意 $k$ 取 $\gamma\in A$ 使 $\gamma(1)=k$，因 $A$ 交换得 $\tau(k)=k$，故 $\tau=\mathrm{id}$。
从而 $H=\{1\}$，于是
\[
|A|=|\mathrm{Orb}_A(1)|\cdot|H|=n.
\]

\textbf{(2)} 由于每个非恒等元素阶为 $p$，$A$ 是指数为 $p$ 的有限 Abel 群，
因此 $A$ 是初等 Abel $p$-群：
\[
A\cong (C_p)^r
\]
某个 $r\ge 1$。于是
\[
|A|=p^r.
\]
结合(1)的 $|A|=n$，得到
\[
n=p^r,\qquad A\cong (C_p)^r.
\]
特别地，对每个 $n$ 若能写成 $p^r$，则同构意义下这样的 $A$ 唯一（就是 $(C_p)^r$）。

\textbf{(3) 构造（平移正则表示）。}
令底集为向量空间 $X=\mathbb F_p^r$（于是 $|X|=p^r=n$），令群 $G=X$ 在自身上作平移：
对 $a\in X$ 定义置换 $T_a(x)=x+a$。
则 $\{T_a\mid a\in X\}$ 构成一个与加法群 $(\mathbb F_p^r,+)$ 同构的 Abel 子群，
且作用显然传递并且每个 $T_a\ (a\neq 0)$ 的阶为 $p$。
由此得到所需嵌入到 $S_n$ 的例子。
\end{solution}
\begin{exercise}[变式3：要求给出元素的循环分解型（考察“正则作用 $\Rightarrow$ 循环长度=元素阶”）]
设 $A\le S_n$ 为 Abel 子群，并且 $A$ 在 $\{1,2,\dots,n\}$ 上传递。
\begin{enumerate}
\item 证明 $|A|=n$，并且该作用是正则的。
\item 设 $\sigma\in A$ 的阶为 $d$，证明 $\sigma$ 的循环分解由恰好 $\frac{n}{d}$ 个互不相交的 $d$-循环组成。
\item 推出：对任意素数 $p\mid n$，存在 $\sigma\in A$，其循环分解为 $\frac{n}{p}$ 个互不相交的 $p$-循环。
\end{enumerate}
\end{exercise}
\begin{solution}
\textbf{(1)} 与变式1(1)同理：点稳定子在 Abel 且传递的情形下只能是平凡群。
从而 $|A|=n$，且除恒等元外无元素有不动点（正则作用）。

\textbf{(2)} 固定 $\sigma\in A$，记其阶为 $d$。
对任意点 $x$，考虑 $\langle\sigma\rangle$ 在点集上的轨道：
\[
x,\ \sigma(x),\ \sigma^2(x),\ \dots
\]
若 $\sigma^t(x)=x$，则 $\sigma^t$ 固定点 $x$。
但由(1)知整个 $A$ 的作用是正则的，故只有恒等元能固定点，
于是 $\sigma^t=\mathrm{id}$，从而 $d\mid t$。
因此 $x$ 在 $\langle\sigma\rangle$ 下的轨道大小恰为 $d$，即 $\sigma$ 的每个循环长度都是 $d$。
全体 $n$ 个点被这些长度为 $d$ 的循环划分，故循环个数为 $\frac{n}{d}$。

\textbf{(3)} 若 $p\mid n$，由(1)得 $|A|=n$，由 Cauchy 定理，$A$ 中存在阶为 $p$ 的元素 $\sigma$。
由(2)立得 $\sigma$ 分解为 $\frac{n}{p}$ 个互不相交的 $p$-循环。
\end{solution}


\begin{problem}[第6题]
记
\[
S=\{(x,y)\mid x^2+y^2=1\},\quad
\Omega=\{(x,y)\mid x^2+y^2\le 1\}.
\]
设 $f:\mathbb{R}^2\to\mathbb{R}$ 为光滑函数，在 $S$ 上恒为 $0$，且满足
\[
\frac{\partial^2 f}{\partial x^2}+\frac{\partial^2 f}{\partial x\partial y}+\frac{\partial^2 f}{\partial y^2}=f^3.
\]
求证：$f$ 在 $\Omega$ 上恒为 $0$。
\end{problem}
\begin{solution}
    记闭盘 $\overline{\Omega}=\{(x,y)\mid x^2+y^2\le1\}$，其边界即单位圆 $S$．$f$ 在 $\mathbb R^2$ 上光滑，故在紧集 $\overline{\Omega}$ 上连续，从而在 $\overline{\Omega}$ 上必能取得最大值和最小值．

\medskip\noindent
\emph{第一步：排除正的内部极大值，证明 $f\le0$.}

设
\[
M=\max_{\overline{\Omega}} f.
\]
若 $M>0$，由于边界 $S$ 上 $f\equiv0$，极大值不能在边界取得，只能在某内部点 $p=(x_0,y_0)\in\Omega$ 处取得，即
\[
f(p)=M,\quad \nabla f(p)=0.
\]

在 $p$ 处，考虑 Hessian 矩阵
\[
Hf(p)=
\begin{pmatrix}
f_{xx}(p) & f_{xy}(p)\\
f_{xy}(p) & f_{yy}(p)
\end{pmatrix}.
\]
因为 $p$ 为局部极大点，对任意方向单位向量 $v$，沿直线 $p+tv$ 的单变量函数
\[
\varphi_v(t)=f(p+tv)
\]
在 $t=0$ 处有极大值，故
\[
\varphi_v''(0)=v^{\mathsf T}Hf(p)v\le0,
\]
即 $Hf(p)$ 为负半定矩阵．

下面用适当方向来表示算子
\[
Lf:=f_{xx}+f_{xy}+f_{yy}
\]
在 $p$ 处的值．取正交单位向量
\[
e_1=\frac1{\sqrt2}(1,1),\qquad e_2=\frac1{\sqrt2}(1,-1).
\]
记 $f_{e_ie_i}(p)$ 为在点 $p$ 沿方向 $e_i$ 的二阶方向导数，则直接按链式法则计算得
\[
f_{e_1e_1}(p)=\frac12\bigl(f_{xx}(p)+2f_{xy}(p)+f_{yy}(p)\bigr),
\]
\[
f_{e_2e_2}(p)=\frac12\bigl(f_{xx}(p)-2f_{xy}(p)+f_{yy}(p)\bigr).
\]
将这两式相加、相减可得
\[
f_{xx}(p)+f_{yy}(p)=f_{e_1e_1}(p)+f_{e_2e_2}(p),
\]
\[
f_{xy}(p)=\frac12\bigl(f_{e_1e_1}(p)-f_{e_2e_2}(p)\bigr).
\]
于是
\[
Lf(p)=f_{xx}(p)+f_{xy}(p)+f_{yy}(p)
      =\bigl(f_{e_1e_1}(p)+f_{e_2e_2}(p)\bigr)
       +\frac12\bigl(f_{e_1e_1}(p)-f_{e_2e_2}(p)\bigr)
      =\frac32\,f_{e_1e_1}(p)+\frac12\,f_{e_2e_2}(p).
\]
在极大点 $p$，前述负半定性给出
\[
f_{e_1e_1}(p)\le0,\quad f_{e_2e_2}(p)\le0,
\]
故
\[
Lf(p)\le0.
\]

然而方程在 $p$ 处给出
\[
Lf(p)=f(p)^3=M^3>0,
\]
与 $Lf(p)\le0$ 矛盾．因此不可能有 $M>0$，从而
\[
M\le0,\quad\text{即 } f(x,y)\le0\quad\forall (x,y)\in\overline{\Omega}.
\]

\medskip\noindent
\emph{第二步：排除负的内部极小值，证明 $f\ge0$.}

类似地，设
\[
m=\min_{\overline{\Omega}} f.
\]
若 $m<0$，边界上 $f\equiv0$，故极小值仍只能在内部某点 $q\in\Omega$ 处取得．在此点 $q$ 处，$Hf(q)$ 为正半定矩阵，故对上述同样的正交基 $e_1,e_2$，有
\[
f_{e_1e_1}(q)\ge0,\quad f_{e_2e_2}(q)\ge0,
\]
从而
\[
Lf(q)=\frac32\,f_{e_1e_1}(q)+\frac12\,f_{e_2e_2}(q)\ge0.
\]
但方程在 $q$ 处给出
\[
Lf(q)=f(q)^3=m^3<0,
\]
矛盾．于是 $m<0$ 不可能，故
\[
m\ge0,\quad\text{即 } f(x,y)\ge0\quad\forall (x,y)\in\overline{\Omega}.
\]

\medskip\noindent
\emph{第三步：综合结论.}

在 $\overline{\Omega}$ 上我们已得到
\[
f\le0\quad\text{且}\quad f\ge0,
\]
故
\[
f\equiv0\quad\text{于 }\overline{\Omega}.
\]
特别地，在 $\Omega$ 上 $f$ 恒为 $0$．

\hfill$\square$
\end{solution}
\begin{exercise}[变式1：改为拉普拉斯算子并加入线性项]
记
\[
S=\{(x,y)\mid x^2+y^2=1\},\quad
\Omega=\{(x,y)\mid x^2+y^2\le 1\}.
\]
设 $f\in C^\infty(\mathbb R^2)$，在 $S$ 上恒为 $0$，且在 $\Omega$ 上满足
\[
\Delta f=f^5+f,\qquad (\Delta f=f_{xx}+f_{yy}).
\]
求证：$f$ 在 $\Omega$ 上恒为 $0$。
\end{exercise}
\begin{solution}
由于 $\overline\Omega$ 紧且 $f$ 连续，$f$ 在 $\overline\Omega$ 上可取到最大值与最小值。
设
\[
M=\max_{\overline\Omega} f,\qquad m=\min_{\overline\Omega} f.
\]
\textbf{(1) 证明 $M\le 0$。}
若 $M>0$，则由于边界上 $f|_{S}\equiv 0$，最大值点不能在边界，只能在内部某点 $P\in \Omega^\circ$ 取得。
在内部极大点 $P$，有 $\nabla f(P)=0$ 且 Hessian 矩阵半负定，故
\[
\Delta f(P)=f_{xx}(P)+f_{yy}(P)\le 0.
\]
但由方程
\[
\Delta f(P)=f(P)^5+f(P)=M^5+M>0,
\]
矛盾。故 $M\le 0$。

\textbf{(2) 证明 $m\ge 0$。}
若 $m<0$，同理最小值点必在内部某点 $Q\in \Omega^\circ$，在内部极小点 $Q$ 有 Hessian 半正定，从而
\[
\Delta f(Q)\ge 0.
\]
但由方程
\[
\Delta f(Q)=f(Q)^5+f(Q)=m^5+m<0,
\]
矛盾。故 $m\ge 0$。

由 $m\ge 0\ge M$ 得 $m=M=0$，因此 $f\equiv 0$ 于 $\Omega$。
\end{solution}
\begin{exercise}[变式2：变系数椭圆型算子（散度形式）]
记
\[
S=\{(x,y)\mid x^2+y^2=1\},\quad
\Omega=\{(x,y)\mid x^2+y^2\le 1\}.
\]
设 $p>1$，$f\in C^\infty(\mathbb R^2)$，在 $S$ 上恒为 $0$，且在 $\Omega$ 上满足
\[
\frac{\partial}{\partial x}\Bigl((1+x^2)\frac{\partial f}{\partial x}\Bigr)
+\frac{\partial}{\partial y}\Bigl((1+y^2)\frac{\partial f}{\partial y}\Bigr)
=|f|^{p-1}f.
\]
求证：$f$ 在 $\Omega$ 上恒为 $0$。
\end{exercise}
\begin{solution}
同样令
\[
M=\max_{\overline\Omega} f,\qquad m=\min_{\overline\Omega} f.
\]

\textbf{(1) $M\le 0$。}
若 $M>0$，由于边界 $S$ 上 $f\equiv 0$，最大值点必在内部某点 $P\in\Omega^\circ$。
在 $P$ 有 $f_x(P)=f_y(P)=0$，且 Hessian 半负定，故 $f_{xx}(P)\le 0,\ f_{yy}(P)\le 0$。
将算子展开：
\[
\frac{\partial}{\partial x}\Bigl((1+x^2)f_x\Bigr)
=(1+x^2)f_{xx}+2x f_x,\qquad
\frac{\partial}{\partial y}\Bigl((1+y^2)f_y\Bigr)
=(1+y^2)f_{yy}+2y f_y.
\]
在点 $P$ 处由于 $f_x(P)=f_y(P)=0$，得
\[
L f(P):=\frac{\partial}{\partial x}\Bigl((1+x^2)f_x\Bigr)(P)
+\frac{\partial}{\partial y}\Bigl((1+y^2)f_y\Bigr)(P)
=(1+x(P)^2)f_{xx}(P)+(1+y(P)^2)f_{yy}(P)\le 0,
\]
因为系数 $1+x^2,1+y^2$ 均为正。
但右端
\[
|f(P)|^{p-1}f(P)=M^p>0,
\]
与 $Lf(P)\le 0$ 矛盾，故 $M\le 0$。

\textbf{(2) $m\ge 0$。}
若 $m<0$，最小值点必在内部某点 $Q\in\Omega^\circ$，则 $f_x(Q)=f_y(Q)=0$ 且 Hessian 半正定，
从而 $f_{xx}(Q)\ge 0,\ f_{yy}(Q)\ge 0$。同样展开得
\[
Lf(Q)=(1+x(Q)^2)f_{xx}(Q)+(1+y(Q)^2)f_{yy}(Q)\ge 0,
\]
但右端
\[
|f(Q)|^{p-1}f(Q)=|m|^{p-1}m<0,
\]
矛盾，故 $m\ge 0$。

于是 $m=M=0$，从而 $f\equiv 0$ 于 $\Omega$。
\end{solution}
\begin{exercise}[变式3：更换常系数椭圆算子与区域（椭圆盘）]
记椭圆边界与其闭内部
\[
S=\Bigl\{(x,y)\,\Bigm|\,\frac{x^2}{4}+y^2=1\Bigr\},\qquad
\Omega=\Bigl\{(x,y)\,\Bigm|\,\frac{x^2}{4}+y^2\le 1\Bigr\}.
\]
设 $f\in C^\infty(\mathbb R^2)$，在 $S$ 上恒为 $0$，并在 $\Omega$ 上满足
\[
f_{xx}-f_{xy}+2f_{yy}=f^3.
\]
求证：$f$ 在 $\Omega$ 上恒为 $0$。
\end{exercise}
\begin{solution}
由于 $\overline\Omega$ 紧，$f$ 在 $\overline\Omega$ 上取到最大最小值，记
\[
M=\max_{\overline\Omega} f,\qquad m=\min_{\overline\Omega} f.
\]

\textbf{(1) 先把算子写成“方向二阶导数 + 正系数项”。}
注意
\[
\Bigl(\partial_x-\tfrac12\partial_y\Bigr)^2 f
=f_{xx}-f_{xy}+\tfrac14 f_{yy},
\]
因此
\[
f_{xx}-f_{xy}+2f_{yy}
=\Bigl(\partial_x-\tfrac12\partial_y\Bigr)^2 f+\frac{7}{4}f_{yy}.
\]
记向量 $v=(1,-\tfrac12)$，则 $\bigl(\partial_x-\tfrac12\partial_y\bigr)^2 f$ 正是 $f$ 在方向 $v$ 的二阶方向导数。

\textbf{(2) 证明 $M\le 0$。}
若 $M>0$，由于边界上 $f|_S\equiv 0$，最大值点必在内部某点 $P\in\Omega^\circ$。
在内部极大点 $P$，对任意方向 $u$ 的二阶方向导数均满足 $D_u^2 f(P)\le 0$。
特别地，
\[
\Bigl(\partial_x-\tfrac12\partial_y\Bigr)^2 f(P)\le 0,\qquad f_{yy}(P)=D_{(0,1)}^2 f(P)\le 0.
\]
因此
\[
\bigl(f_{xx}-f_{xy}+2f_{yy}\bigr)(P)
=\Bigl(\partial_x-\tfrac12\partial_y\Bigr)^2 f(P)+\frac{7}{4}f_{yy}(P)\le 0.
\]
但由方程得
\[
\bigl(f_{xx}-f_{xy}+2f_{yy}\bigr)(P)=f(P)^3=M^3>0,
\]
矛盾。故 $M\le 0$。

\textbf{(3) 证明 $m\ge 0$。}
若 $m<0$，最小值点必在内部某点 $Q\in\Omega^\circ$，在内部极小点有对任意方向 $u$，$D_u^2 f(Q)\ge 0$。
于是
\[
\Bigl(\partial_x-\tfrac12\partial_y\Bigr)^2 f(Q)\ge 0,\qquad f_{yy}(Q)\ge 0,
\]
从而
\[
\bigl(f_{xx}-f_{xy}+2f_{yy}\bigr)(Q)\ge 0,
\]
但方程给出
\[
\bigl(f_{xx}-f_{xy}+2f_{yy}\bigr)(Q)=f(Q)^3=m^3<0,
\]
矛盾。故 $m\ge 0$。

综上 $m=M=0$，所以 $f\equiv 0$ 于 $\Omega$。
\end{solution}


\begin{problem}[第7题]
设
\[
C=\{a=(a_1,a_2,\ldots,a_n,\ldots)\mid a_i\in\{0,1\}\},\qquad \Phi:C\to[0,1].
\]
称满足以下条件的 $\Phi$ 为正则的：若对任意 $a\in C$ 和任意 $\varepsilon>0$，存在正整数 $N$，使得：若数列 $b=(b_1,b_2,\ldots,b_n,\ldots)$ 满足 $a_n=b_n$ 对 $n\le N$ 成立，则 $|\Phi(a)-\Phi(b)|<\varepsilon$。  
设 $f:[0,1]\to[0,1]$，求证：$f$ 连续当且仅当对任意正则的 $\Phi$，均有 $f\circ \Phi$ 正则。
\end{problem}
\begin{solution}
    先给出一个度量：
\[
d(a,b)=\sum_{n=1}^{\infty}\frac{|a_n-b_n|}{2^n},\qquad a=(a_n),\,b=(b_n)\in C.
\]
则 $(C,d)$ 为紧度量空间，且上述“正则”条件恰好就是 $\Phi$ 关于 $d$ 的连续性：
\[
\forall a,\ \forall\varepsilon>0,\ \exists N:\ d(a,b)<2^{-N}\Rightarrow|\Phi(a)-\Phi(b)|<\varepsilon.
\]
因此“正则”$\Longleftrightarrow$“连续”。

\medskip
\noindent\emph{充分性：$f$ 连续 $\Rightarrow$ $f\circ\Phi$ 正则.}

设 $f:[0,1]\to[0,1]$ 连续，$\Phi:C\to[0,1]$ 正则（即连续）．则复合
\[
f\circ\Phi:C\longrightarrow[0,1]
\]
是连续映射（拓扑学中连续映射的复合仍连续）．由上面的等价性，$f\circ\Phi$ 即为正则．

\medskip
\noindent\emph{必要性：若对任意正则 $\Phi$，$f\circ\Phi$ 都正则，则 $f$ 必连续.}

反设 $f$ 在某点 $x_0\in[0,1]$ 不连续．则存在数列 $x_k\in[0,1]$，使得
\[
x_k\to x_0,\qquad f(x_k)\not\to f(x_0).
\]
于是存在 $\varepsilon_0>0$ 及子列（仍记为 $x_k$），使
\[
|f(x_k)-f(x_0)|\ge\varepsilon_0,\quad \forall k.
\]

在 $C$ 中构造如下点列：
\[
a^*=(0,0,0,\dots),
\]
\[
a^{(k)}=(\underbrace{0,\dots,0}_{k\ \text{个}},1,0,0,\dots),\quad k\ge1.
\]
显然 $a^{(k)}\to a^*$（在 $d$ 下，两者前 $k$ 项相同）．令
\[
F=\{a^*\}\cup\{a^{(k)}:k\ge1\}\subset C.
\]
则 $F$ 是闭集（$\{a^{(k)}\}$ 的唯一极限点 $a^*$ 也包含在 $F$ 中）．

在 $F$ 上定义函数
\[
g:F\to[0,1],\qquad g(a^*)=x_0,\quad g(a^{(k)})=x_k.
\]
注意到 $a^*$ 是 $F$ 中唯一的极限点，而 $x_k\to x_0$，故 $g$ 在 $a^*$ 处连续；其余点孤立处连续平凡成立，因此 $g$ 在 $F$ 上连续．

由 Tietze 延拓定理（$C$ 为紧度量空间，故为正规空间，$[0,1]$ 为闭区间），存在连续延拓
\[
\Phi:C\to[0,1],\quad \text{使得}\ \Phi|_F=g.
\]
因而 $\Phi$ 为正则映射．

对 $a^*$ 及数列 $a^{(k)}$ 有
\[
\Phi(a^*)=x_0,\quad \Phi(a^{(k)})=x_k.
\]
对任意 $\varepsilon=\varepsilon_0/2>0$，若 $f\circ\Phi$ 在 $a^*$ 处正则，则存在 $N$，使得 $b$ 与 $a^*$ 前 $N$ 项相同时
\[
|(f\circ\Phi)(b)-(f\circ\Phi)(a^*)|<\varepsilon.
\]
但对所有 $k>N$，$a^{(k)}$ 与 $a^*$ 前 $N$ 项相同，且
\[
|(f\circ\Phi)(a^{(k)})-(f\circ\Phi)(a^*)|
=|f(x_k)-f(x_0)|\ge\varepsilon_0> \varepsilon,
\]
矛盾．故存在正则 $\Phi$ 使得 $f\circ\Phi$ 非正则，从而推出 $f$ 必连续．

综上，$f$ 连续当且仅当对任意正则 $\Phi$，复合 $f\circ\Phi$ 也是正则．$\square$
\end{solution}
\begin{exercise}[变式1：推广到多元函数]
令
\[
C=\{a=(a_1,a_2,\dots)\mid a_i\in\{0,1\}\},
\]
称 $\Phi:C\to[0,1]^m$ \emph{正则}，若对任意 $a\in C$ 与任意 $\varepsilon>0$，存在正整数 $N$，使得对一切 $b\in C$，
若 $a_n=b_n$ 对 $n\le N$ 成立，则
\[
\|\Phi(a)-\Phi(b)\|<\varepsilon
\]
（$\|\cdot\|$ 为欧氏范数）。
设 $f:[0,1]^m\to[0,1]$，证明：$f$ 连续当且仅当对任意正则的 $\Phi:C\to[0,1]^m$，复合 $f\circ \Phi$ 正则。
\end{exercise}
\begin{solution}
\textbf{($\Rightarrow$)} 若 $f$ 连续且 $\Phi$ 正则（即在此拓扑/度量意义下连续），则复合 $f\circ\Phi$ 连续，
因此满足题设的正则性定义。

\textbf{($\Leftarrow$)} 反设 $f$ 不连续。则存在 $x_0\in[0,1]^m$，$\varepsilon_0>0$，以及点列 $x^{(j)}\to x_0$ 使得
\[
|f(x^{(j)})-f(x_0)|\ge \varepsilon_0,\qquad \forall j.
\]
取子列仍记为 $x^{(j)}$ 使得
\[
\|x^{(j)}-x_0\|<2^{-j},\qquad \forall j.
\]
定义 $\Phi:C\to[0,1]^m$ 如下：对 $a\in C$，令
\[
N(a)=\min\{j\ge 1:\ a_j=1\}\quad(\text{若不存在则 }N(a)=\infty).
\]
并规定
\[
\Phi(a)=
\begin{cases}
x_0,&N(a)=\infty\ (\text{即 }a=(0,0,0,\dots)),\\
x^{(N(a))},&N(a)<\infty.
\end{cases}
\]

\emph{验证 $\Phi$ 正则。}
\begin{itemize}
\item 若 $N(a)=r<\infty$，则一切与 $a$ 在前 $r$ 项相同的 $b$ 都满足 $N(b)=r$，故 $\Phi(b)=\Phi(a)$，于是连续（正则）。
\item 若 $a=(0,0,0,\dots)$，给定 $\varepsilon>0$，取 $N$ 使 $2^{-N}<\varepsilon$。
若 $b_1=\cdots=b_N=0$，则 $N(b)>\!N$ 或 $N(b)=\infty$，从而
\[
\|\Phi(b)-\Phi(a)\|\le \|x^{(N(b))}-x_0\|<2^{-N}<\varepsilon.
\]
\end{itemize}
故 $\Phi$ 正则。

但对 $b^{(j)}=(\underbrace{0,\dots,0}_{j-1},1,0,0,\dots)$，有 $b^{(j)}$ 与全零序列在前 $j-1$ 项相同，
且
\[
(f\circ\Phi)(b^{(j)})=f(x^{(j)}),\qquad (f\circ\Phi)(0,0,\dots)=f(x_0),
\]
于是
\[
|(f\circ\Phi)(b^{(j)})-(f\circ\Phi)(0,0,\dots)|
=|f(x^{(j)})-f(x_0)|\ge \varepsilon_0.
\]
这表明 $f\circ\Phi$ 在全零序列处不正则，矛盾。
因此 $f$ 必连续。
\end{solution}

\begin{exercise}[变式2：只需检验一个“特定”的正则满射]
令
\[
C=\{a=(a_1,a_2,\dots)\mid a_i\in\{0,1\}\},
\qquad
\Pi:C\to[0,1],\quad \Pi(a)=\sum_{n=1}^\infty \frac{a_n}{2^n}.
\]
\begin{enumerate}
\item 证明 $\Pi$ 是正则的且为满射；
\item 设 $f:[0,1]\to[0,1]$，证明：$f$ 连续当且仅当 $f\circ \Pi$ 正则。
\end{enumerate}
\end{exercise}
\begin{solution}
\textbf{(1) $\Pi$ 正则且满射。}

\emph{正则性：}若 $a_n=b_n$ 对 $n\le N$ 成立，则
\[
|\Pi(a)-\Pi(b)|
\le \sum_{n>N}\frac{|a_n-b_n|}{2^n}
\le \sum_{n>N}\frac{1}{2^n}
=\frac{1}{2^N},
\]
故给定 $\varepsilon>0$ 取 $N$ 使 $2^{-N}<\varepsilon$ 即得正则。

\emph{满射性：}给定 $x\in[0,1]$，递归定义 $r_0=x$，并对 $n\ge 1$ 定义
\[
a_n=\lfloor 2r_{n-1}\rfloor\in\{0,1\},\qquad r_n=2r_{n-1}-a_n\in[0,1).
\]
则
\[
r_{n-1}=\frac{a_n+r_n}{2}
\quad\Longrightarrow\quad
x=\sum_{j=1}^n\frac{a_j}{2^j}+\frac{r_n}{2^n}.
\]
令 $n\to\infty$，因 $0\le r_n<1$，有 $\frac{r_n}{2^n}\to 0$，故
\[
x=\sum_{j=1}^\infty \frac{a_j}{2^j}=\Pi(a),
\]
从而 $\Pi$ 满射。

\textbf{(2) 连续性等价。}

\emph{($\Rightarrow$)} 若 $f$ 连续且 $\Pi$ 正则，则 $f\circ\Pi$ 正则。

\emph{($\Leftarrow$)} 设 $f\circ\Pi$ 正则。取任意闭集 $F\subset[0,1]$，
则
\[
\Pi^{-1}\bigl(f^{-1}(F)\bigr)=(f\circ\Pi)^{-1}(F)
\]
在 $C$ 中为闭集（因 $f\circ\Pi$ 正则即连续）。
由于 $C$ 紧，故 $\Pi^{-1}(f^{-1}(F))$ 亦为紧集，其像
\[
\Pi\bigl(\Pi^{-1}(f^{-1}(F))\bigr)
\]
为 $[0,1]$ 中的紧集，从而为闭集（因为 $[0,1]$ 为 Hausdorff）。
又因 $\Pi$ 满射，对任意集合 $A\subset[0,1]$ 有 $\Pi(\Pi^{-1}(A))=A$，
因此
\[
f^{-1}(F)=\Pi\bigl(\Pi^{-1}(f^{-1}(F))\bigr)
\]
是闭集。
故 $f$ 对任意闭集的原像都闭，即 $f$ 连续。
\end{solution}

\begin{exercise}[变式3：点态版本（连续于一点的刻画）]
仍令
\[
C=\{a=(a_1,a_2,\dots)\mid a_i\in\{0,1\}\},\qquad \mathbf 0=(0,0,0,\dots)\in C.
\]
称映射 $\Phi:C\to[0,1]$ 在点 $\mathbf 0$ \emph{正则}，若对任意 $\varepsilon>0$，存在 $N$，使得对一切 $b\in C$，
若 $b_1=\cdots=b_N=0$，则
\[
|\Phi(b)-\Phi(\mathbf 0)|<\varepsilon.
\]
设 $f:[0,1]\to[0,1]$，给定 $x_0\in[0,1]$。证明：
\[
f \text{ 在 }x_0\text{ 连续}
\quad\Longleftrightarrow\quad
\text{对任意满足 }\Phi(\mathbf 0)=x_0\text{ 且在 }\mathbf 0\text{ 正则的 }\Phi,\ f\circ\Phi\text{ 在 }\mathbf 0\text{ 正则}.
\]
\end{exercise}
\begin{solution}
\textbf{($\Rightarrow$)} 若 $f$ 在 $x_0$ 连续，且 $\Phi(\mathbf 0)=x_0$ 并在 $\mathbf 0$ 正则，
则当 $b\to\mathbf 0$（即足够长前缀为 $0$）时 $\Phi(b)\to x_0$，从而
\[
(f\circ\Phi)(b)\to f(x_0)=(f\circ\Phi)(\mathbf 0),
\]
即 $f\circ\Phi$ 在 $\mathbf 0$ 正则。

\textbf{($\Leftarrow$)} 反设 $f$ 在 $x_0$ 不连续。
则存在 $\varepsilon_0>0$ 以及点列 $x_j\to x_0$ 使得
\[
|f(x_j)-f(x_0)|\ge \varepsilon_0,\qquad \forall j.
\]
取子列仍记为 $x_j$，使得
\[
|x_j-x_0|<2^{-j},\qquad \forall j.
\]
定义 $\Phi:C\to[0,1]$：
\[
\Phi(a)=
\begin{cases}
x_0,&a=\mathbf 0,\\
x_{N(a)},&N(a)=\min\{j\ge 1:\ a_j=1\}<\infty.
\end{cases}
\]
与变式1同理可证：$\Phi(\mathbf 0)=x_0$ 且 $\Phi$ 在 $\mathbf 0$ 正则
（若 $b_1=\cdots=b_N=0$，则 $N(b)>N$ 或 $=\infty$，从而 $|\Phi(b)-x_0|<2^{-N}$）。

然而对 $b^{(j)}=(\underbrace{0,\dots,0}_{j-1},1,0,0,\dots)$，有 $b^{(j)}\to\mathbf 0$，
且
\[
|(f\circ\Phi)(b^{(j)})-(f\circ\Phi)(\mathbf 0)|
=|f(x_j)-f(x_0)|\ge \varepsilon_0,
\]
故 $f\circ\Phi$ 在 $\mathbf 0$ 不正则，矛盾。
因此 $f$ 在 $x_0$ 必连续。
\end{solution}




\begin{problem}[第8题]
设 $f:\mathbb{Q}\to[0,+\infty)$，满足：  
(a) $f(x)=0$ 当且仅当 $x=0$；  
(b) $f(xy)=f(x)f(y),\ \forall x,y\in\mathbb{Q}$；  
(c) $f(x+y)\le \max\{f(x),f(y)\}$；  
(d) $f$ 至少有 $3$ 个不同的取值。  

求证：  
(1) $f(1)=f(-1)=1$，且对任意整数 $k$，均有 $f(k)\le 1$ 成立；  
(2) 当 $f(x)\ne f(y)$ 时，$f(x+y)=\max\{f(x),f(y)\}$；  
(3) 对于 $x\ne 0$，存在 $\alpha\in(0,1)$ 和素数 $p$，使得 $f(x)=\alpha^{\nu_p(x)}$。
\end{problem}
\begin{solution}
    设 $f:\mathbb{Q}\to[0,+\infty)$ 满足 (a)(b)(c)(d).

\medskip
\noindent(1)\quad 先求 $f(1),f(-1)$ 并估计整数值.

由 (b) 得
\[
f(1)=f(1\cdot1)=f(1)^2,
\]
因 $f(1)\ge0$，故 $f(1)=0$ 或 $1$．若 $f(1)=0$，则由 (a) 得 $1=0$，矛盾．故
\[
f(1)=1.
\]

由 (b) 得
\[
f(-1)^2=f((-1)\cdot(-1))=f(1)=1.
\]
又 $f(-1)\ge0$，故
\[
f(-1)=1.
\]

任取整数 $k$，先考虑 $k\ge0$，用归纳证明 $f(k)\le1$．显然 $f(0)=0\le1$，且 $f(1)=1$．设对某 $n\ge1$，已有 $f(n)\le1$，则
\[
f(n+1)=f(n+1-0)\le \max\{f(n),f(0)\}=\max\{f(n),0\}\le1,
\]
其中用到 (c)．于是对所有非负整数 $k$ 有 $f(k)\le1$．

对负整数 $k=-n<0$，由 (b) 及 $f(-1)=1$ 得
\[
f(k)=f(-n)=f((-1)\cdot n)=f(-1)f(n)=f(n)\le1.
\]
故对任意整数 $k$，均有 $f(k)\le1$．

\medskip
\noindent(2)\quad 若 $f(x)\ne f(y)$，则
\[
f(x+y)=\max\{f(x),f(y)\}.
\]

由 (c) 先知
\[
f(x+y)\le \max\{f(x),f(y)\}.
\]
不妨设 $f(x)<f(y)$．再看
\[
y=(x+y)+(-x).
\]
由 (c) 得
\[
f(y)=f((x+y)+(-x))\le\max\{f(x+y),f(-x)\}
=\max\{f(x+y),f(x)\}.
\]
若 $f(x+y)<f(y)$，则右侧 $\max\{f(x+y),f(x)\}\le f(y)$ 且两个都 $<f(y)$，这将与 $f(y)\le\max\{f(x+y),f(x)\}$ 矛盾．
因此只能有
\[
f(x+y)\ge f(y).
\]
结合先前的不等式 $f(x+y)\le\max\{f(x),f(y)\}=f(y)$，得到
\[
f(x+y)=f(y)=\max\{f(x),f(y)\},
\]
命题得证．

\medskip
\noindent(3)\quad 对于 $x\ne0$，存在 $\alpha\in(0,1)$ 及素数 $p$，使
\[
f(x)=\alpha^{\nu_p(x)}.
\]

先注意到，由 (a)(b)(c) 可将 $f$ 视为一个\emph{非阿基米德绝对值}．定义
\[
|x|:=f(x),\qquad x\in\mathbb{Q}.
\]
则

\begin{itemize}
  \item 由 (a) 知 $|x|=0$ 当且仅当 $x=0$；
  \item 由 (b) 知 $|xy|=|x||y|$；
  \item 由 (c) 知 $|x+y|\le\max\{|x|,|y|\}$（超三角不等式）；
  \item 由 (d) 知 $|\cdot|$ 非平凡（因为取值不少于 $3$ 种，不是只有 $0,1$ 两值）．
\end{itemize}

因此 $|\cdot|$ 是 $\mathbb{Q}$ 上一个非平凡的非阿基米德绝对值．

另一方面，由 (1) 知对任意整数 $k$ 有 $|k|=f(k)\le1$，故对所有素数 $p$ 也有 $|p|\le1$，这说明该绝对值\emph{不}与通常的实值绝对值等价．

由 Ostrowski 定理关于 $\mathbb{Q}$ 上非阿基米德绝对值的分类：任一非平凡非阿基米德绝对值必与某个 $p$ 进绝对值 $|\cdot|_p$ 等价，即存在常数 $c>0$ 与某个素数 $p$，使得
\[
|x|=|x|_p^{\,c},\qquad \forall x\in\mathbb{Q}.
\]
记
\[
\alpha:=|p|_p^{\,c}=p^{-c}\in(0,1).
\]
由 $p$ 进绝对值的性质，若 $x\in\mathbb{Q}^\times$，其 $p$ 进赋值为 $\nu_p(x)$（即 $x=p^{\nu_p(x)}\frac{m}{n}$，其中 $m,n$ 与 $p$ 互素），则
\[
|x|_p=p^{-\nu_p(x)}.
\]
于是
\[
f(x)=|x|=|x|_p^{\,c}=(p^{-\nu_p(x)})^{c}=p^{-c\nu_p(x)}=(p^{-c})^{\nu_p(x)}=\alpha^{\nu_p(x)}.
\]

这就说明：存在素数 $p$ 与 $\alpha\in(0,1)$，使得对一切 $x\in\mathbb{Q}$（特别是 $x\ne0$）都有
\[
f(x)=\alpha^{\nu_p(x)}.
\]

\hfill$\square$
\end{solution}

\begin{exercise}[变式1：只给通常三角不等式，要求推出“强三角不等式”并分类]
设 $f:\mathbb{Q}\to[0,+\infty)$ 满足：
\begin{enumerate}
\item[(a)] $f(x)=0$ 当且仅当 $x=0$；
\item[(b)] $f(xy)=f(x)f(y),\ \forall x,y\in\mathbb{Q}$；
\item[(c)]（通常三角不等式）$f(x+y)\le f(x)+f(y),\ \forall x,y\in\mathbb{Q}$；
\item[(d)]（整数有界性）对任意整数 $n$，$f(n)\le 1$；
\item[(e)] $f$ 至少有 $3$ 个不同的取值。
\end{enumerate}
证明：
\begin{enumerate}
\item[(1)] $f(1)=f(-1)=1$；
\item[(2)]（推出强三角不等式）对任意 $x,y\in\mathbb{Q}$，
\[
f(x+y)\le \max\{f(x),f(y)\},
\]
并且当 $f(x)\ne f(y)$ 时，$f(x+y)=\max\{f(x),f(y)\}$；
\item[(3)] 存在素数 $p$ 与 $\alpha\in(0,1)$ 使得对任意 $x\in\mathbb{Q}$，
\[
f(x)=\alpha^{\nu_p(x)}\quad(\text{约定 }f(0)=0,\ \nu_p(0)=+\infty).
\]
\end{enumerate}
\end{exercise}

\begin{solution}
\textbf{(1)} 由 (b) 取 $x=y=1$ 得 $f(1)=f(1)^2$，故 $f(1)\in\{0,1\}$。
若 $f(1)=0$ 则对任意 $x$，$f(x)=f(x\cdot 1)=f(x)f(1)=0$，与 (a) 矛盾，故 $f(1)=1$。
又由 $(-1)^2=1$：
\[
1=f(1)=f((-1)^2)=f(-1)^2 \Rightarrow f(-1)=1.
\]

\textbf{(2)} 先证强三角不等式。对任意正整数 $n$，由二项式定理
\[
(x+y)^n=\sum_{k=0}^{n}\binom{n}{k}x^k y^{n-k}.
\]
用 (b) 与 (c) 得
\[
f(x+y)^n=f((x+y)^n)\le \sum_{k=0}^n f\!\left(\binom{n}{k}\right) f(x)^k f(y)^{n-k}.
\]
而 $\binom{n}{k}\in\mathbb{Z}$，由 (d) 有 $f(\binom{n}{k})\le 1$，于是
\[
f(x+y)^n\le \sum_{k=0}^n f(x)^k f(y)^{n-k}\le (n+1)\cdot M^n,
\quad M:=\max\{f(x),f(y)\}.
\]
故
\[
f(x+y)\le (n+1)^{1/n} M.
\]
令 $n\to\infty$，因 $(n+1)^{1/n}\to 1$，得
\[
f(x+y)\le M=\max\{f(x),f(y)\}.
\]

再证“值不同则取大者”。不妨设 $f(x)<f(y)$。
由已证强三角不等式
\[
f(x+y)\le \max\{f(x),f(y)\}=f(y).
\]
另一方面 $y=(x+y)-x$，再用强三角不等式得
\[
f(y)\le \max\{f(x+y),f(x)\}=\max\{f(x+y),\text{更小的 }f(x)\}=f(x+y),
\]
故 $f(x+y)=f(y)=\max\{f(x),f(y)\}$。

\textbf{(3)} 由 (e) 知 $f$ 不可能只取 $\{0,1\}$ 两值。
若对所有非零整数 $n$ 都有 $f(n)=1$，则对任意 $x=\frac{a}{b}\in\mathbb{Q}^\times$，
\[
f(x)=\frac{f(a)}{f(b)}=1,
\]
从而 $f$ 仅取 $\{0,1\}$，与 (e) 矛盾。
因此存在整数 $N>1$ 使 $f(N)<1$。

令
\[
I:=\{n\in\mathbb{Z}: f(n)<1\}.
\]
与变式2同理（此处仍可用已知强三角不等式替代原 (c)），可得 $I$ 是 $\mathbb{Z}$ 的非零素理想，
故 $I=p\mathbb{Z}$（$p$ 为素数）。
于是 $p\nmid n \Rightarrow n\notin I \Rightarrow f(n)\not<1$，再由 (d) 得 $f(n)=1$。
设 $\alpha:=f(p)\in(0,1)$。

对任意 $n\ne 0$，写 $n=u p^{k}$ 且 $p\nmid u$，则 $f(u)=1$，从而
\[
f(n)=f(u)f(p)^k=\alpha^{\nu_p(n)}.
\]
对任意 $x=\frac{a}{b}\in\mathbb{Q}^\times$，
\[
f(x)=\frac{f(a)}{f(b)}=\alpha^{\nu_p(a)-\nu_p(b)}=\alpha^{\nu_p(x)}.
\]
并且 $f(0)=0$。证毕。
\end{solution}