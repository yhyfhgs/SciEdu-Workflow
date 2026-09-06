## 线性方程组与矩阵

## 2．1．线性方程组与消元法

议 $m, n$ 为正警数，域 $F$ 上包含 $m$ 个方程的 $n$ 元线性方程组们如下形式：

$$
\left\{\begin{array}{c}
a_{11} x_{1}+a_{12} x_{2}+\cdots+a_{1 n} x_{n}=b_{1} \\
a_{21} x_{1}+a_{22} x_{2}+\cdots+a_{2 n} x_{n}=b_{2} \\
\cdots \cdots \cdots \cdots \\
a_{m 1} x_{1}+a_{m 2} x_{2}+\cdots+a_{m n} x_{n}=b_{m}
\end{array}\right.
$$

其中 $x_{1}, x_{2}, \cdots, x_{n}$ 为木知量，$u_{1 j}, l_{,} \in F, \iota=1,2, \cdots, m, J=1,2, \cdots, u$ 。我们称 $u_{1 j} x_{j}$ 为第 $i$ 个方程的第 $j$ 项，$u_{, j}$ 为第 $i$ 个方程中知量 $\pi$ ，或者项 $a_{i}, r$ ，的系数。 $v_{1}$ 为第 $i$ 个方程的常数项。若方程组（1）中常数项 $b_{1}, ~ b_{2} \cdots, ~ b_{m}$ 均为零。则称（1）是一个齐次方程组，否则称之为非齐次方程组。

设 $c_{1}, c_{2}, \cdots, c_{n} \in F$ 、若代入 $x_{1}=c_{1}, r_{2}=c_{2}, \cdots, x_{n}=c_{11}$ 后方程组（1）中的每个等式均成立。则称 $x_{1}=c_{1}, x_{2}=c_{2}, \cdots, x_{n}=c_{n}$ 是线性方程组（1）的一个（或一组）解。方程组 （1）的所有解构成的集合称为该方程组的解杹．若域 $F$ 上具有相 $|r|$ 未知坴的两个线性方程组解集相同，则称其是同解的。

我们引入以下三头线性方程组的初等变换：
－互换：交换两个方程在方程组中的位置。设 $i, j$ 足 1 到 $m$ 之问的两个相异整数。交换方程组（1）中第 $i$ 个方程和第 $j$ 个方程的位置是指从方程组（1）得到一个新方程组，该新方程组除第 $i$ 个方程和第 $j$ 个方程分别为方程组（1）的第 $j$ 个方程和第 $i$ 个方程外，共余方程及它们在方程组中的位置均与方程组（1）相问。
－倍乘：将 $F$ 中一个非零常数乘以某个方程．设；是 1 到 $m$ 之间的一个整数。 $c \in F$ 为非零元素，将 $c$ 乘以方程组（1）中第 $i$ 个方程是指从方程组（1）得到一个新方程组，该新方程组除第 $l$ 个方程换为方程

$$
c a_{11} x_{1}+c a_{12} x_{22}+\cdots+c a_{1 n} x_{n}=c b_{1}
$$

外、其余方程及它们在方程组中的位置均与方程组（1）相 rm ．
－倍加。将一个方程乘以一个常数压加到另一个方程上去。议 $L, J$ 为 1 到 $m$ 之间的两个相异整数.$~ c \in F$ 为常数。将 $c$ 乘以方程组（1）中第 $z$ 个方程后加到第 $j$ 个方程 上．去是指从方程组（1）楾到－个新方程组、该断方程组除第 $j$ 个方程换为方程

$$
\left(a_{11}+c a_{11}\right) x_{1}+\left(a_{12}+c a_{12}\right) x_{2}-\cdots+\left(a_{11}+c a_{1 n}\right) x_{n}=b_{1}+c b_{1}
$$

外。其余方程及它们在方程组中的位置均与方理组（1）相同。

不难验证下述肌理成 $\%$ 。

引理2．1．1．线性方程组初等变换盯后的两个方程组是同解的

定理 2．1．2．域 $F$ 上形如（1）式的方程组均同解于如下形式的阶梯形线性方程组：

其中空白部分表示相关项的系数全为零，$r$ 为满足 $0 \leqslant r \leqslant \min \{m, n\}$ 的某个非负骆数。 $1 \leqslant j_{1}<j_{2}<\cdots<j_{r} \leqslant n$ 且 $b_{1,1}, b_{2,2}, \cdots$ ，$b_{r j}$ ，均不为零。

让意当 $r=0$ 时方程组（2）中左运系数全为答；当 $r=m$ 时，方程组（2）中形式为 $0=c_{r-1}$ 以及 $0=0$ 的方程不出现。

下对方程介数 $m$ 进行り $\exists$ 纳：$m=1$ 时，已形成（2）形式
君已知对 $m-1$ 个方程的立，下考急 $m$ 时，仅考息左式系数不全为 0则 $\exists$ 最小下标 $j$ 使 $a_{i j}$ ，不财为 0 且 $k<j$ ，时 $a_{i k}=0$
记 $k 0$ 为最小下标使 $a_{k a j} \neq 0$
![](https://cdn.mathpix.com/cropped/9df01145-b796-4202-bf1f-3a439e99df65-02.jpg?height=168&width=1244&top_left_y=1571&top_left_x=497)

则方程组
![](https://cdn.mathpix.com/cropped/9df01145-b796-4202-bf1f-3a439e99df65-02.jpg?height=162&width=793&top_left_y=1649&top_left_x=817)

$$
\left\{\begin{array}{l}
a_{(j)}^{\prime} \hat{N}_{\lambda}^{\prime} 0_{[j, 1}^{\prime} \lambda_{j, n}^{\prime}+\ldots=b_{1}^{\prime} \\
00_{2 j, n}^{\prime} \lambda_{1,1}^{\prime} \ldots=\bar{b}_{2}^{\prime \prime} \\
0
\end{array}\right.
$$

个为程，由リヨ纳即得

我们叮进一步通过付方程组（2）实施数乘和倍加初等变换，将方程佪（2）化为形式更为简单的同解方程组。

推论 2．1．3．城 $F$ 上形加（1）式的方程组均同解丁如下形式的阶梯形线性方程组：

$$
\left\{\begin{aligned}
c_{1,1} x_{11}-\cdots+c_{1,1} x_{11}+\cdots+c_{1,1} x_{1,1}+\cdots+c_{1, r} x_{1,}+\cdots+c_{1 n} x_{n} & =d_{1} \\
c_{3,1} x_{1,}+\cdots+c_{1,0} x_{11}+\cdots+c_{2, r} \jmath_{1,}+\cdots+c_{2 n} x_{n} & =d_{2} \\
c_{3,1} r_{1,1}+\cdots+c_{1,1} x_{1,}+\cdots+c_{3 n} x_{n} & =d_{d} \\
\cdots \cdots \cdots \cdots & \\
c_{r,-} x_{1,}+\cdots+c_{r n} x_{n} & =d_{r} \\
0 & =d_{r-1} \\
0 & =0 \\
\cdots \cdots \cdots \cdots & \\
0 & =0
\end{aligned}\right.
$$

其巾空白解分表示㕲关项的系数全为零，$r$ 为满足 $0 \leqslant r \leqslant \min \{m, \pi\}$ 的某个非负整数， $1 \leqslant j_{1}<j_{2}<\cdots<j_{1} \leqslant n$ ，对 $1 \leqslant k \leqslant r$ 。有 $c_{k j u}=1$ 且其止上方系数 $c_{1 j u}=0,1 \leqslant i<k$ 。

定理 2．1．4．议城 $F$ 上的线性方程细（1）经 Gauss 消元后化为方程组（3）、则
－方程组（1）有解当且仅当 $r=m$ 或 $c<m$ 但 $d_{r+1}=0$ ；方程组（1）无解当目仅当 $r<m$ 且 $d_{r+1} \neq 0$.
－当方程组（1）有解时，
（i）方程组（1）们唯一解当且仅当 $r=n$ ，且此时唯一解为

$$
\iota_{1}-d_{1}, \iota_{2}-d_{2}, \cdots, \iota_{n}-d_{n}
$$

（ii）方程组（1）有多个解当且仅当 $r<n$ ，此时记 $\{1,2, \cdots, n\} \backslash\left\{j_{1}, j_{2}, \cdots, j_{r}\right\}= \left\{k_{1}, k_{2}, \cdots, k_{n-r}\right\}$ ，其中 $k_{1}<k_{2}<\cdots<k_{n-r}$ ，则方程组（1）的所有解为

$$
\left\{\begin{aligned}
x_{\jmath_{1}} & =d_{1}-c_{1 k_{1}} l_{1}-c_{1 k_{2}} l_{2}-\cdots-c_{1 k_{n-r}} l_{n-r} \\
r_{\jmath 2} & =d_{2}-c_{2 h_{1}} l_{1}-c_{2 k_{2}} l_{2}-\cdots-c_{3 k_{n-1}} l_{1 k-r} \\
\cdots \cdots \cdots \cdots & \\
r_{\jmath_{r}} & =d_{r}-c_{1 k_{1}} l_{1}-c_{r k_{2}} l_{2}-\cdots-c_{r k_{n-1},} l_{n-r} \\
r_{k_{1}} & =t_{1} \\
r_{k_{2}} & =t_{2} \\
\cdots \cdots \cdots \cdots & \\
r_{k_{n-r}} & =l_{n-r}
\end{aligned}\right.
$$

其中 $l_{1}, l_{2} \cdots, l_{n-r}$ 为 $F$ 中任意元萘，称为 $[1$ 由变军。称十式为方程的通解。

诚然、带筸未知同消元较为察现，我们将给出更为简洁的求解方式，即矩阵表达形式 现将方程组水解的消元法提珎为矩阼的消元法。并也称为 Gauss 消元法。这 一方法可以川结为矩阵的一类初等变换，它反咉了方程组消元法的本顺，也是知阵理论的巩要［其

将线性方程组（1）左边未知量的所有系数取出。保持它们在方程组中的机对位置不变升构成矩阵

$$
A=\left(a_{1 j}\right)_{m \times n}=\left(\begin{array}{cccc}
a_{11} & a_{12} & \cdots & a_{1 n} \\
a_{21} & l_{22} & \cdots & a_{2 n} \\
\vdots & \vdots & & \vdots \\
a_{m 1} & a_{m 2} & \cdots & a_{m n}
\end{array}\right)
$$

称为线性方程组（1）的系数迎阵。令 $x=\left(\begin{array}{c}x_{1} \\ x_{2} \\ \vdots \\ x_{n}\end{array}\right) . b=\left(\begin{array}{c}b_{1} \\ b_{2} \\ \vdots \\ b_{m}\end{array}\right)$ ，那么利用矩阵乘法可以将方程组（1）等价地表示为矩阵方程 $A x=b$ 。系数矩阵 $A$ 与常数项向屋 $b$ 可构成矩阵

$$
\Lambda_{h} \stackrel{\text { def }}{=}(A \mid b)=\left(\begin{array}{cccc:c}
a_{11} & a_{12} & \cdots & a_{1 n} & b_{1} \\
a_{21} & a_{22} & \cdots & a_{2 n} & b_{2} \\
\vdots & \vdots & & \vdots & \vdots \\
a_{m 1} & a_{m 2} & \cdots & a_{n n} & b_{m}
\end{array}\right)
$$

称为线性方程组（1）的增＂矩阵。
对应于线性方程组的三类初等变换。现纴我们削入矩阵相应的初等行变换（以及类似的初等列变换））。从而把方程组的 Gauss 消元法归结为矩降的 Gauss 消元法、即我们可以通过矩阵的初等行变换来简化线性方程组的求解。

为方便起见、我们称矩阵两行中们同列元素或两列中的同行元素为对应元素。

定义2．1．5．【初等变换】域 $F$ 上矩阵的初等行（列）变换是指对矩阵实施如下之一的变换。
－互换：交换矩阵的两行（列）对应元素的位置．简称两行（列）互换．第 $i$ 行（列）与 $j$ 第 $j$行（列）的巫换记作 $R_{i j}\left(C_{1,}\right)$ 。
－倍乘：将 $F$ 中非零常数 $a$ 乘以矩阵的某行（列）的每 一个元索，简称倍乘某行（列）用 $a$ 去倍乘第 $i$ 行（列）的倍乘。记作 $a R_{1}\left(a C_{1}\right)$ 。
－倍加：将某行（列）的篈一个元素乘以同一常数加到另一行（列）的对应元素上。第 j 行 （列）的各元素乘以 $a$ 加列第 $i$ 行（列）对应元素上的倍加，记作 $R_{1}+a R_{3}\left(C_{1}+a C_{1}\right)$ ，简称为第 $j$ 行（列）乘以 $a$ 加到第 $i$ 行（列）。

那么线性方程组（1）经消元法后化为方程组（3）的过程，实际上就是对增广旿阵 $A_{6}$ 实

觔阿等行贫换的过程：

$$
\Lambda_{b} \xrightarrow{\text { 㠺部存治 }}\left(\begin{array}{ccccccccc:c}
c_{1,1} & \cdots & c_{1,2} & \cdots & c_{1,1} & \cdots & c_{1,1} & \cdots & c_{1 n} & d_{1} \\
& & c_{2,1} & \cdots & c_{2,1} & \cdots & c_{2,1} & \cdots & c_{2 n} & d_{2} \\
& & & & c_{1,1} & \cdots & c_{1,1} & \cdots & c_{3 n} & d_{4} \\
& & & & & & \vdots & & \vdots & \vdots \\
& & & & & & c_{r, 1} & \cdots & c_{r n} & d_{r} \\
& & & & & & & & & d_{r+1}
\end{array}\right)
$$

这里箭头冇侧矩阵是方程组（3）的增广知阵，其中空白处元素均为零、对 $1 \leqslant k \leqslant r$ ，们 $c_{k_{j,}}=1$ 且其正上方元素 $c_{1 j \mathrm{~h}}, 1 \leqslant i<k$ 也均为蓉。利用矩阵初等行变换过程米刻响线性方程细的消元过程简化了计算过程中的表达方式。这样的转换也使得我们可以方便地利用计算机实施线性方程组的数值求解。

## 2．2．矩阵基本概念

定义2．2．1．｜拒阵］设 $m_{1} n$ 是两个正整数．我们称由域 $F$ 中的 $n L n$ 个元素 $a_{1},(i= 1,2, \cdots, m, j=1,2, \cdots, n)$ 形成的元萗阵列

$$
\left(a_{1, j}\right)_{m \times n}=\left(\begin{array}{cccc}
a_{11} & a_{12} & \cdots & a_{1 n} \\
a_{21} & a_{22} & \cdots & a_{2 n} \\
\vdots & \vdots & & \vdots \\
a_{r n 1} & a_{m 2} & \cdots & a_{m n}
\end{array}\right)
$$

为 $F$ 上的 • 个 $m \times \pi$ 矩阵．

设 $A=\left(a_{1 j}\right)_{m \times n} \in F^{m \times n} 、 B=\left(b_{1 j}\right)_{r \times n} \in F^{r / A}$ 。若 $m=r . n=s$ 且 $a_{1 j}=b_{1 j}(i= 1,2, \cdots, m . j=1,2, \cdots, n)$ ，则称 $A$ 」 $B$ 相等，记作 $A=B$ 。

下面我们讨论饭阵的加减法，数乘、乘法、转置运算、以及由此产牛的代数结构。

定义 2．2．2．设 $A=\left(a_{i \jmath}\right)_{m \times n}, B=\left(b_{i \jmath}\right)_{m \times n}, C=\left(c_{i \jmath}\right)_{m \times n}$ 为 $F^{m \times n}$ 中能阵。若 $c_{1 j}=a_{1 j} \pm b_{1 j}(i=1,2, \cdots, m, j=1,2, \cdots, n)$ 、则称 $C^{\prime}$ 是 $A$ 与 $B$ 的和（差）、记作 $C^{\prime}=A \pm B$ 。这样的运算称为矩阵的加法（减法）运算。

设 $A=\left(a_{1,}\right)_{m \times n} . B=\left(b_{1,}\right)_{m \times n}$ 为 $F^{m \times n}$ 中矩阵。 $\lambda \in F$ ．若 $b_{1}=\lambda a_{1,}(i= 1.2, \cdots, m, j=1.2, \cdots, n)$ ．则称 $B$ 为 $\lambda$ 与 $A$ 的积，记作 $B=\lambda A$ ．这样的运算称为元

- 零矩阵 $O^{\text {man }}$ ：元东全为 $O$
- 单位阵 $\begin{aligned} & I_{n}: \\ & (E)\end{aligned}$ Inm $=\left(\begin{array}{cccc}1 & 0 & \cdots & 0 \\ 0 & 0 & \cdots & 0 \\ 0 & 0 & 1 & 0 \\ 0 & -2 & 0 & 0 \\ 0 & 0 & 0 & 0\end{array}\right)$
- $E_{j}{ }^{1 \text { 行 }-(-i) \quad a_{k s}=\left\{\begin{array}{l}1 \\ 0\end{array}, k+j, \text { 自 } 5=j \text { 或 } 5 \neq j \text {（需阵迷跂）}\right.}$
- 对角阵 $\quad\left(\begin{array}{cccc}\hat{1}_{1} & 0 & -0 & 0 \\ 0 & \lambda_{2} & 0 & 0 \\ 0 & 0 & 0 & 0 \\ 0 & 0 & 0 & 0\end{array}\right)=\operatorname{dig}\left(\lambda_{1} \lambda_{2} \ldots x_{n}\right)$

彔 $\lambda$ 与知阵 $A$ 的数乘运算。

## 域 $F$ 上的矩阵集 $F^{m \times n}$ 大下加法和数乘是 个 $F$ 上的线性劣问

定义 2．2．3．称 $F^{m \times p}$ 中起阵 $C=\left(r_{1}\right)_{m \times p}$ 为 $F^{m \times n}$ 中矩阵 $A=\left(a_{k!}\right)_{m \times n}$ L $_{\mathrm{j}} F^{n \times p}$ 中起阵 $B=\left(b_{k j}\right)_{n \times p}$ 的积，并记作 $C=A B$ ，如果

$$
c_{1 j}=a_{1} b_{11}+a_{12} b_{2 j}+\cdots+a_{1 n} b_{1 n j}=\sum_{k=1}^{\prime \prime} a_{1 k} b_{k j}, \quad i=1.2, \cdots, m, j=1.2, \cdots, p,
$$

或者衣ぶ为

$$
\left(\begin{array}{ccccc}
c_{11} & \cdots & c_{11} & \cdots & c_{1 p} \\
\vdots & & \vdots & & \vdots \\
c_{11} & \cdots & c_{11} & \cdots & c_{i p} \\
\vdots & & \vdots & & \vdots \\
c_{m 1} & \cdots & c_{m 1} & \cdots & c_{m p}
\end{array}\right)=\left(\begin{array}{ccccc}
a_{11} & \cdots & a_{1 n} & \cdots & a_{1 n} \\
\vdots & & \vdots & & \vdots \\
\hdashline a_{i 1} & \cdots & a_{1 k} & \cdots & a_{1 n} \\
\hdashline \vdots & & \vdots & & \vdots \\
a_{m 1} & \cdots & \left(l_{m k}\right. & \cdots & a_{m n}
\end{array}\right)\left(\begin{array}{cc:c:cc}
b_{11} & \cdots & b_{1 j} & \cdots & b_{1 p} \\
\vdots & & \vdots & & \vdots \\
b_{k 1} & \cdots & b_{k j} & \cdots & b_{k p} \\
\vdots & & \vdots & & \vdots \\
b_{n 1} & \cdots & b_{n j} & \cdots & b_{n p}
\end{array}\right)
$$

这样的运算过程称为矩阵的乘法．
只行光矩阵 $A$ 的列数和 $B$ 的行数相等时，$A$ 和 $B$ 才能相乘，即 $A B$ 才有总义。特別地、同阶的方阵总可以相乘。无 $A B=B A$ 充要条件

定义 2．2．4．设 $F$ 是域，$A \in F^{n \times n}$ ．若存在 $B \in F^{n \times n}$ 使得

$$
A B=B A=I_{n},
$$

则称 $A$ 是可逆的或非奇异的．并称 $B$ 为 $A$ 的逆矩阵．若不存在 $B \in F^{n \times n}$ 使上式成立、则称 $A$ 是不可逆的或奇异的。

例 2．2．1．实矩阵 $\left(\begin{array}{ll}1 & 0 \\ 1 & 1\end{array}\right)$ 和 $\left(\begin{array}{ll}1 & 0 \\ 0 & 2\end{array}\right)$ 均可逆，其逆矩阵分别是 $\left(\begin{array}{rr}1 & 0 \\ -1 & 1\end{array}\right)$ 和 $\left(\begin{array}{ll}1 & 0 \\ 0 & \frac{1}{2}\end{array}\right)$ 但矩阵 $\left(\begin{array}{ll}0 & 0 \\ 1 & 1\end{array}\right)$ 不可逆．

定理 2．2．5．若域 $F$ 上的 $n$ 阶方阵 $A$ 可逆。则 $A$ 的逆矩阵唯
右 $B \cdot C$ 为 $A$ 的逆降，$B=B \cdot I_{n}=B \cdot(A C)=(B \cdot A) \cdot C=I_{n}-C=C$
对域 $F$ 上的可逆方阵 $A$ ，记 $A$ 唯一的逆矩阵为 $A^{-1}$ 。当域 $F$ 上的 $n$ 阶方阵 $A$ 和 $B$ 㓅逆时，下列运算规律成立：
－$\left(A^{-1}\right)^{-1}=A$ ；
－$\underline{(A B)^{-1}=B^{-1} A^{-1}} \quad \Leftrightarrow\left(B^{-1} A^{-1}\right)(A B)=\operatorname{In} \quad \Leftrightarrow\left(B^{-1} \operatorname{In}\right) \quad B=\operatorname{In}$ 部立
－$(\lambda A)^{-1}=\lambda^{-1} A^{-1}, \lambda \in F^{\mathrm{x}}$ ．
将 $F$ 上所有 $n$ 阶可逆矩阵之集记为 $\mathrm{GL}_{n}(F)$ 。集合 $\mathrm{GL}_{n}(F)$ 关于矩阵乘法构成一个群，其单位元是单位矩阵 $I_{n}$ 、且每个 $A \in \mathrm{GL}_{n}(F)$ 的逆元就是 $A$ 的逆矩阵 $A^{-1}$ 。称为 $F$ 上的 $n$ 阶一般线性眻、且为非交换群。

定义 2．2．6．设 $A=\left(a_{i j}\right)_{m \times n} \in F^{m \times n}, B=\left(b_{i j}\right)_{n \times m} \in F^{n \times m}$ ，若 $b_{i j}=a_{j i}(i= 1,2, \cdots, n, j=1,2, \cdots, m)$ ，则称 $B$ 是 $A$ 的转置矩阵，记作 $B=A^{\top}$ 。我们称 $A \mapsto A^{\top}$ 为矩阵的转置运算。转置体现了矩阵行与列的对称性。

性质 2．2．7 不难验证 $F$ 上矩阵的转置运算具有以下性质：
－$\left(A^{\top}\right)^{\top}=A$ ；
－若 $A 、 B \in F^{m \times n}$ ，则 $(A \pm B)^{\top}=A^{\top} \pm B^{\top}$ ；
－$(\lambda A)^{\top}=\lambda A^{\top}, \lambda \in F$ ；

- 若 $A \in F^{m \times n}, B \in F^{n \times p}$ ，则 $(A B)^{\top}=B^{\top} A^{\top}$
- 若 $A \in F^{n \times n}$ 可逆，则 $\left(A^{\top}\right)^{-1}=\left(A^{-1}\right)^{\top}$ ．


## 2．3．分块矩阵

定义 2．3．1．设 $A \in F^{m \times n}$ 沿行的方向自上而下将行分成 $r$ 个部分，沿列的方向从左到右将列分成 $s$ 个部分，从而将 $A$ 分划成 $r s$ 个子部分。设 $A_{t j}(i=1,2, \cdots, r, j=1,2, \cdots, s)$ 表示由行的第 $i$ 个部分与列的第 $j$ 个部分交叉处的元素保持原来位置关系不变所形成的矩阵 （称之为 $A$ 的子块）．我们称形式 $A=\left(A_{1 j}\right)_{r \times n}$ 为一个 $r \times s$ 分块矩阵。

本节当中主要讨论分块矩阵的一些运算．

性质 2．3．2．设 $A, B \in F^{m \times n}$ 经适当分块后成为

$$
A=\left(\begin{array}{cccc}
A_{11} & A_{12} & \cdots & A_{1 s} \\
A_{21} & A_{22} & \cdots & A_{2 s} \\
\vdots & \vdots & & \vdots \\
A_{r 1} & A_{r 2} & \cdots & A_{r s}
\end{array}\right) . \quad B=\left(\begin{array}{cccc}
B_{11} & B_{12} & \cdots & B_{1 g} \\
B_{21} & B_{22} & \cdots & B_{2 \jmath} \\
\vdots & \vdots & & \vdots \\
B_{r 1} & B_{12} & \cdots & B_{r g}
\end{array}\right),
$$

其中 $A_{1}$ 与 $B_{1}$, 则为 $m_{1} \times n$ ，矩阵 $\left(i=1,2, \cdots, r_{1} j=1,2, \cdots, 0\right)$ ，且 $m=\sum_{i=1}^{r} m_{1}, n=\sum_{j=1}^{n} n_{1}$ ，即 $A . B$ 的行与列分块方式均相同。那么由定义显然有

$$
A \pm B=\left(\begin{array}{cccc}
A_{11} \pm B_{11} & A_{12} \pm B_{12} & \cdots & A_{1 a} \pm B_{1 a} \\
A_{21} \pm B_{21} & A_{22} \pm B_{22} & \cdots & A_{2 a} \pm B_{2 a} \\
\vdots & \vdots & & \vdots \\
A_{r 1} \pm B_{r 1} & A_{r 2} \pm B_{r 2} & \cdots & A_{r a} \pm B_{r a}
\end{array}\right) .
$$

以及对 $\lambda \in F$ 有数躬

$$
\lambda A=\left(\begin{array}{cccc}
\lambda A_{11} & \lambda A_{12} & \cdots & \lambda A_{10} \\
\lambda A_{21} & \lambda A_{22} & \cdots & \lambda A_{20} \\
\vdots & \vdots & & \vdots \\
\lambda A_{r 1} & \lambda A_{r 2} & \cdots & \lambda A_{r 0}
\end{array}\right)
$$

性质 2．3．3．下面讨论分块矩阵的乘法和转置。
设 $A \in F^{m \times n}$ 与 $B \in F^{n \times p}$ 经适当分块后成为

$$
A=\left(\begin{array}{cccc}
A_{11} & A_{12} & \cdots & A_{1 n} \\
A_{21} & A_{22} & \cdots & A_{2 n} \\
\vdots & \vdots & & \vdots \\
A_{r 1} & A_{r 2} & \cdots & A_{r n}
\end{array}\right), \quad B=\left(\begin{array}{cccc}
B_{11} & B_{12} & \cdots & B_{1 t} \\
B_{21} & B_{22} & \cdots & B_{3 t} \\
\vdots & \vdots & & \vdots \\
B_{n 1} & B_{n 2} & \cdots & B_{n t}
\end{array}\right)
$$

其中 $A_{i k}$ 为 $m_{i} \times n_{k}$ 矩阵 $(i=1,2, \cdots, r, k=1,2, \cdots, s), B_{k j}$ 为 $n_{k} \times p$ ，矩阵 $(k= 1.2, \cdots, s, j=1,2, \cdots, t)$ ，且 $m=\sum_{i=1}^{r} m_{i}, n=\sum_{k=1}^{0} n_{k}, p=\sum_{j=1}^{t} p_{j}$ 。即 $A$ 的列分块方式与 $B$的行分块方式相同．那么不难证明

$$
\begin{aligned}
A B & =\left(\sum_{k=1}^{0} A_{i k} B_{k j}\right)_{r \times t} \\
& =\left(\begin{array}{ccc}
A_{11} B_{11}+A_{12} B_{21}+\cdots+A_{1 s} B_{s 1} & \cdots & A_{11} B_{1 t}+A_{12} B_{2 t}+\cdots+A_{1 a} B_{a t} \\
A_{21} B_{11}+A_{22} B_{21}+\cdots+A_{2 s} B_{a 1} & \cdots & A_{21} B_{1 t}+A_{22} B_{2 t}+\cdots+A_{2 s} B_{a t} \\
\vdots & & \vdots \\
A_{r 1} B_{11}+A_{r 2} B_{21}+\cdots+A_{r s} B_{a 1} & \cdots & A_{r 1} B_{1 t}+A_{r 2} B_{3 t}+\cdots+A_{r k} B_{a t}
\end{array}\right)
\end{aligned}
$$

对于分块矩阵的转置，苦 $A \in F^{m \times n}$ 分块形如本性质前式，则

$$
A^{\top}=\left(\begin{array}{cccc}
A_{11}^{\top} & A_{21}^{\top} & \cdots & A_{r 1}^{\top} \\
A_{12}^{\top} & A_{22}^{\top} & \cdots & A_{r 2}^{\top} \\
\vdots & \vdots & & \vdots \\
A_{1 n}^{\top} & A_{20}^{\top} & \cdots & A_{r 0}^{\top}
\end{array}\right)
$$

## 2．4．初等矩阵与标准型

本节中我们将讨论矩阵的初等变换和对应的初等矩阵，并利用它们给出矩阵的标准形。我们首先定义初等矩阵。设 $E_{l j}, i, j=1,2, \cdots, n$ 为仅 $a_{v j}$ 为列，拖沓项为 0 的 $n$ 阶方阵。对域 $F$ 上的 $n$ 阶单位陏 $i_{\text {，}}$ 实施一次初等行（或列）变换、有 1

定义 2．4．1［初等规阵］
（i）第 $i$ 行（列）和第 $j$ 行（列）互换
![](https://cdn.mathpix.com/cropped/9df01145-b796-4202-bf1f-3a439e99df65-09.jpg?height=241&width=522&top_left_y=1046&top_left_x=1007)

$$
I_{n} \xrightarrow[\text { Le } C_{i j}]{R_{i j}} P_{i j} \stackrel{\text { dof }}{=} I_{n}-E_{i l}-E_{j j}+E_{i j} \|_{\text {列 }}+E_{j i,} \quad i \neq j .
$$

（ii）第 $i$ 行（列）倍乘非零元素 $a$

$$
\begin{aligned}
& i \text { 行 }-\left(-\cdots a_{1} a_{1}\right) \\
& \cdots, 1, \underbrace{a}_{\text {mi } \text { LA }}, 1, \cdots, i\}, \quad a \in F^{\prime} .
\end{aligned}
$$

![](https://cdn.mathpix.com/cropped/9df01145-b796-4202-bf1f-3a439e99df65-09.jpg?height=264&width=1561&top_left_y=1649&top_left_x=189)
上述 $n$ 阶方阵 $P_{t j}^{a_{i j}}, D_{i}(a)$ 椞 $T_{i j}(a)$ 分别称为互换 $R_{t j}\left(C_{t j}\right)$ ，倍乘 $a R_{1}\left(a C_{1}\right)$ 以及倍加 $R_{1}+a R_{j}\left(C_{j}+a C_{1}\right)$ 所对应的 $n$ 阶初等矩阵，也分别称为第一类，第二类和第三类初等矩阵。

性质 2．4．2．初等矩阵均可逆、且其逆矩阵仍为初等矩阵。具体有

$$
\left\{\begin{array}{l}
P_{i j}^{-1}=P_{i j}, \quad i \neq j . \\
D_{i}(a)^{-1}=D_{i}\left(a^{-1}\right), \quad a \in F^{\times}, \\
T_{i j}(a)^{-1}=T_{i j}(-a), \quad i \neq j, a \in F .
\end{array}\right.
$$

后 $A$ 可逆，则 $\exists B, A B=B A=1 n$

$$
x=\left(A I_{n}\right) \quad B x=(A B A B)=\left(I_{n} \circ B\right)
$$

$B$ 可视为子力等矩阵乘积

定理 2．4．3．对矩阵实施一次初等行（或列）变换得到的矩阵等于用该初等变换对应的初等矩阵左饿（或右剩）原矩阵得到的积。

定理 2．4．4．对任意 $A \in F^{m \times n}$ ，存在 $m$ 阶初等矩阵 $P_{1}, P_{2}, \cdots, P_{k}$ 和 $n$ 阶初等矩阵 $Q_{1} 、 Q_{2} 、 \cdots, ~ Q_{1}$ 以及満足 $0 \leqslant r \leqslant \min (m, n)$ 的整数 $r$ 使得

$$
P_{l} \cdots P_{2} P_{1} A Q_{1} Q_{2} \cdots Q_{l}=\left(\begin{array}{cc}
I_{r} & O \\
O & O
\end{array}\right)
$$

从而由性质 2．4．2 知存在 $m$ 阶可逆矩阵 $P$ 和 $n$ 阶可逆矩阵 $Q$ 使得

$$
P A Q=\left(\begin{array}{ll}
l_{r} & 0 \\
0 & 0
\end{array}\right)
$$

推论 2．4．5．可逆矩阵均可表示为初等矩阵之积。
![](https://cdn.mathpix.com/cropped/9df01145-b796-4202-bf1f-3a439e99df65-10.jpg?height=98&width=1120&top_left_y=1218&top_left_x=483)
（i）若 $r=n \Rightarrow p^{-1}(p A Q) \cdot a^{-1}=p^{-1} \cdot I r \cdot a^{-1}$
$A=P^{-1} \cdot Q^{-1}$ 枝为水刀等矩阵积
（ii）：右rcn，记 $A$ 迷驺 $x \Rightarrow A x=I_{n}=A Q O^{-1} x$

$$
\begin{aligned}
& P I_{n} P^{-1}=(P A G)\left(Q^{-1} \times P^{-1}\right)=I_{n}=\left(\begin{array}{ll}
I_{r} & 0
\end{array}\right)\left(Q^{-1} \times P^{-1}\right) \\
& i Z a^{-1} \times P^{-1}=\left(\begin{array}{cc}
A \text { rar } & B \\
C & D
\end{array}\right) \quad\left(\begin{array}{cc}
{ }^{\text {r }} & 0
\end{array}\right)\left(\begin{array}{cc}
\left.\begin{array}{cc}
A \text { arb } & B \\
C & D
\end{array}\right)=\left(\begin{array}{cc}
\operatorname{Ara} & B \\
0 & 0
\end{array}\right)=I_{n} \text { 矛盾! }
\end{array}\right. \text { }
\end{aligned}
$$

若 $A$ 可通过初等：变换化为矩阵 $B=\left(\begin{array}{ll}I_{r} & O \\ O & O\end{array}\right)$ ，则称 $B$ 为 $A$ 的标准形．
定理 2．4．6．任意矩阵的，标准形都是唯一的。

若 $\exists r=s, A=P_{1}\left(I_{r} 0\right) Q_{1}=P_{2}\left(I_{s} 0\right) Q_{2}$
不交力列

$$
\Rightarrow p_{2}^{-1} p_{1}\left({ }^{I r_{0}} 0\right)=\left({ }^{I s} 0\right) a_{2} Q_{1}
$$

$$
\begin{aligned}
& i d m=p_{2}^{-1} p_{1}=\left(\begin{array}{ll}
x_{s a r} & Y_{s a(m-r)} \\
Z_{1 m-s) a r} & W(m-s) \mu(m-r)
\end{array}\right) \\
& N=a_{2} a_{1}^{-1}=\left(\begin{array}{cc}
x_{s 4 s}^{\prime} & Y_{s u(n-s)}^{\prime} \\
Z_{(n-s) u s}^{\prime} & W_{(n-s) \sin s}^{\prime}
\end{array}\right) \\
& \Rightarrow\binom{x_{s a r} 0}{Z_{(m-s) n} 0}=\left(\begin{array}{cc}
x^{\prime} s_{s} & Y^{\prime} \sin (n-s) \\
0 & 29
\end{array}\right) \\
& \therefore Z_{(m-s) \mu}=0, Y^{\prime} \sec (n-s)=0
\end{aligned}
$$

$$
\begin{array}{ll}
N_{n n_{n}}= & \left(\begin{array}{ll}
(x & 0)_{\text {sess }}^{\prime} \\
(n O)_{n}=1 \\
Z_{(n-s)+s}^{\prime} & W_{(n-s)(n-s)}
\end{array}\right)
\end{array}
$$

if $x D=\left(x^{1}, x^{2}, \ldots x^{r}, 0, \ldots 0\right)$
$x^{\prime \prime}=\left(\begin{array}{llll}e_{11} & e_{12} & e_{001 s} \\ e_{21} & e_{22} & e_{2 s} \\ e_{n 1} & e_{n 2} & e_{n s} \\ e_{n 1} & e_{n 2} & e_{n s}\end{array}\right)$
$\Rightarrow(x O) x^{\prime \prime}=\left(e_{11} x^{\prime} \cdot e_{21} x^{2} \cdot-1 e_{n 1} 00, e_{10} x^{1} \cdot e_{2} x^{2} 1-\right.$
$\left.{ }^{\prime} \operatorname{en} 20, \ldots\right)>T$
Is 可祀为列向量线现关，啲列向量物为
$x^{\prime}, x^{2}, \ldots x^{\top}$ 线性组分，的XT列向鲜组提
![](https://cdn.mathpix.com/cropped/9df01145-b796-4202-bf1f-3a439e99df65-10.jpg?height=193&width=1392&top_left_y=2697&top_left_x=434)

例 2．4．1．求下列非异阵 $A$ 的迎阵：

$$
\begin{gathered}
A=\left(\begin{array}{lll}
1 & 2 & 3 \\
2 & 1 & 2 \\
1 & 3 & 4
\end{array}\right) \\
\xrightarrow{\left(\begin{array}{cccc}
1 & 2 & 3 & 1 \\
2 & 1 & 2 \\
1 & 3 & 4 & \\
1
\end{array}\right) \xrightarrow[T_{31}]{T_{21}(-1)}(-1)}\left(\begin{array}{ccccc}
1 & 2 & 3 & 1 & 0 \\
0 & -3 & -4 & -2 & 1 \\
0 & 1 & 1 & -1 & 0 \\
0 & 1
\end{array}\right) \xrightarrow[\substack{T_{33}(-2) \\
10}]{T_{23}(3)}\left(\begin{array}{ccccc}
10 & 1 & 3 & 0 & -2 \\
0 & 0 & -1 & -5 & 1 \\
0 & 1 & 1 & -1 & 0 \\
0 & 1 & 3 & 0 & -2 \\
0 & 0 & 1 & 5 & -1 \\
0 & 1 & -3
\end{array}\right) \\
\Rightarrow A^{-1}=\left(\begin{array}{cccc}
-2 & 1 & 1 \\
-6 & 14 \\
5 & 1 & 3
\end{array}\right)
\end{gathered}
$$

## 2．5．矩阵秩与向量组的秩

上一章末尾我们知道了矩阵的标准形是唯一的，从而我们引入矩阵的秩，并由此给出可逆矩阵的一个刻画。

定义 2．5．1．［积］苦 $B-\left(\begin{array}{cc}I_{\mathrm{r}} & O \\ O & O\end{array}\right)$ 是 $A_{m \times n}$ 的标准形，则称 $r$ 为 $A$ 的秩，记为 $r(A)$ 或 $\operatorname{rank} A$ ．

显然 $0 \leqslant r\left(A_{m \times n}\right) \leqslant \min \{m, n\}$ ，且 $r(A)=0$ 当且仅当 $A=0$ ．
矩阵的佚是矩阵理论的一个重要概念，它将贯穿于本课程的学习、根据矩阵的秩，我们引入矩阵等价的概念。

定义 2．5．2．［萝秩］设 $A, B \in F^{m \times n}$ ，若 $r(A)=r(B)$ ，则称 $A$ 与 $B$ 是等秋的，记作 $A \stackrel{R}{\sim} B$ ．

定理 2．5．3．$A \stackrel{R}{\sim} B$ 当且仅当 $A$ 可经初等变换化为 $B$ 。
$A, B \in F^{m \times n}$ 等秩的充要条件是 $A B$ 相抵。即存在 $F$ 上 $m$ 阶可逆莓阵 $P$ 和 $n$ 阶可逆矩阵 $Q$ 使得 $P A Q=B$ 。专 $r(A)=r(B) \quad \exists P_{1} A Q_{1}=\binom{\text { rr } Q}{0 O} \quad P_{2} B Q_{3}=\binom{\text { Fr }}{0}$

$$
\therefore P_{1} A O_{0}=P_{2} B Q_{2} \Rightarrow P^{-1} P_{1} A Q_{1} Q_{2}^{-}=B
$$

特别地，对于方阵的情形，我们可以用秩和初等矩阵给出可逆矩阵的刻画。

定义 2．5．4．对 $A \in F^{n \times n}$ ，下列陈述等价：
（1）$A$ 是可逆的：
（ii）$A$ 是满秩的，即 $r(A)=n$ ：
（Iii）存在可逆矩阵 $P, Q \in F^{n \times n}$ 使得 $P A Q=I_{n}$ ；
（iv）$A$ 可写为初等矩阵之积：

$$
\begin{aligned}
& L \\
& A=P^{-} \operatorname{In} Q^{-}=P^{-} Q^{-} \Rightarrow \\
& \\
& \dot{A} \& I P A Q P=\operatorname{In} \theta
\end{aligned}
$$

（v）存在初等矩阵 $P_{1}, P_{2}, \cdots, P_{k} \in F^{n \times n}$ 使得 $P_{k} \cdots P_{2} P_{1} A=I_{n}$ ；
（vi）存在初等矩阵 $Q_{1}, Q_{2}, \cdots, Q_{1} \in F^{n \times n}$ 使得 $A Q_{1} Q_{2} \cdots Q_{1}=I_{n}$ ．
最后我们叙述利用矩阵初等变换米计算秩的方法。事实上，要确定矩阵的秩并不需要将其化为标准形，只需将其化为阶梯形线性方程组（2）系数矩阵的形式即可。可知对 $A \in F^{m \times n}$ ，存在 $0 \leqslant r \leqslant \min \{m, n\}$ 使得

$$
A \xrightarrow{\text { 呀算效 }} B=\left(\begin{array}{cccccccccc}
\cdots & b_{1 j_{1}} & \cdots & b_{1 j_{2}} & \cdots & b_{1 j_{2}} & \cdots & b_{1 i_{r}} & \cdots & b_{1 n} \\
& & & b_{2 j_{2}} & \cdots & b_{2 j_{2}} & \cdots & b_{1 r} & \cdots & b_{1 n} \\
& & & & & b_{3 j_{2}} & \cdots & b_{3 j_{r}} & \cdots & b_{3 n} \\
& & & & & & & \vdots & & \vdots \\
& & & & & & & b_{r j_{r}} & \cdots & b_{r n} \\
& & & & & & & & &
\end{array}\right),
$$

其中空白位置元素均为零且 $b_{1 j_{1}}, b_{2 j_{2}}, \cdots, b_{r j}$ ，均非零。那么 $A$ 的秩即为 $r$ 。
矩降的秩与通过极大线性无关组定义的向量组的秩之问存在管本顺的联系。这样的联系既反映了矩阵的柣与向量组的秩的特性，也显示了秩的概念将矩阵理论与线性空间理论紧密联系在一起。接下氷我们讨论 $F^{m \times n}$ 中矩阵的秩与列向量空间 $F^{m \prime}$ 和 $F^{n}$ 中向量组的秩之间的联系。

设 $A=\left(a_{i j}\right)_{m \times n} \in F^{m \times n}$, 对 $A$ 按行分块得 $A=\left(\begin{array}{c}\alpha_{1} \\ \alpha_{2} \\ \vdots \\ \alpha_{m}\end{array}\right)$ ．其中 $\alpha_{1}=\left(a_{i 1}, a_{i 2}, \cdots\right.$, $\left.a_{i n}\right), i=1,2, \cdots, m$ 是 $F^{1 \times n}$ 中的 $m$ 个向量。称为 $A$ 的行向量．此时 $F^{1 \times n}$ 的子空间 $\operatorname{Span}\left\{\alpha_{1}, \alpha_{2}, \cdots, \alpha_{m}\right\}$ 称为．$A$ 的行空间，$A$ 的行向量组的柣即为 $A$ 的行空间的维数，称为 $A$ 的行秩．类似地，对 $A$ 按列分块得 $A=\left(\begin{array}{llll}\beta_{1} & \beta_{2} & \cdots & \beta_{n}\end{array}\right)$ ，其中 $\beta_{j}=\left(\begin{array}{c}a_{1,} \\ a_{2,} \\ \vdots \\ a_{m,}\end{array}\right), j= 1,2, \cdots, n$ 是 $F^{m \times 1}$ 中的 $n$ 个向量，称为 $A$ 的列向量。称 $F^{m \times 1}$ 的子空间 $\operatorname{Span}\left\{\beta_{1}, \beta_{2}, \cdots, \beta_{n}\right\}$为 $A$ 的列空间，其维数称为 $A$ 的列秩．

引理 2．5．5．矩阵的初等行（列）变换不改变矩阵的行（列）秋。
下证行变换不改文行钦
$\binom{\alpha_{1}}{\alpha_{m}} \rightarrow\binom{\alpha_{1}}{\gamma_{m}}$ ，只需证：行的量等价
（1）：对换，$\alpha_{1} \alpha_{j}$ 对换，$\left\{\alpha_{1} \ldots \alpha_{m}\right\}=\left\{\beta_{1} \ldots \beta_{m}\right\}$
（2）：份息；$\gamma_{1}=\alpha \cdot \alpha_{i} ; \alpha_{1}=\frac{1}{\alpha} \gamma_{i}$ 就等你
（3）但加 $-\gamma_{j}=\alpha_{0} a_{i}, \alpha_{j}, \alpha_{j}=\gamma_{j}-\alpha a_{i}=\sigma_{j}-\alpha \gamma_{i}$ 等价

根据定义，向量组的柣是其极大线性无关组中的向量个数．由此不难证明：

引理 2．5．6．若 $\alpha_{1}, \alpha_{3}, \cdots, \alpha_{m} \in F^{1 \times n}$ ，则 $r\left(\alpha_{1}, \alpha_{2}, \cdots, \alpha_{m}\right)=r\left(\alpha_{1}^{\top}, \alpha_{2}^{\top}, \cdots, \alpha_{m}^{\top}\right)$ ．
$A^{\text {中行秩 }}=A^{\top} \phi$ 列铁
定理 2．5．7．域 $F$ 上任意矩阵 $A$ 的秩，行秩和列佚二者相等，且 $r(A)=r\left(A^{\top}\right)$ ．
![](https://cdn.mathpix.com/cropped/9df01145-b796-4202-bf1f-3a439e99df65-13.jpg?height=78&width=1265&top_left_y=1338&top_left_x=740)
上述结论说明、矩阵的秩也可以用矩阵的行（列）秩米定义。我们知道对 $X \in F^{m \times n}$ ，总 $\Rightarrow a_{1 s_{1}=1} a_{k s i}=O(k<1)$有 $0 \leqslant r(A) \leqslant \min \{m, n\}$ 。若 $r(A)=m$ ，则称 $A$ 是行满秩的，此时必然有 $m \leqslant n$ ．类似地若耉有 乐数 $\left\{C_{i}\right\}$ 。 $r(A)=n$ ，则称 $A$ 是列满秩的，此时必然有 $n \leqslant m$ ．

性质 2．5．8．由定理 2．5．3，若 $P \in F^{m \times m}, Q \in F^{n \times n}$ 均可逆，$A \in F^{m \times n}$ ，则

$$
r(P A)=r(A Q)=r(P A Q)=r(A) .
$$

为方便叙述，$F$ 上矩阵 $A$ 的列空间和行空间分别记为 $V_{A}$ 和 $W_{A}$ ，从而 $r(A)=\operatorname{dim} V_{A}= \operatorname{dim} W_{\lambda}$ 。由矩阵乘法可直接验证如下引理：设 $A \in F^{m \times n}$ ，则 $V_{A}=\left\{A . x \mid x \in F^{n \times 1}\right\}, W_{A}= \left\{y A \mid y \in F^{1 \times m}\right\}$ 。 一般地，对矩阵乘法我们有
![](https://cdn.mathpix.com/cropped/9df01145-b796-4202-bf1f-3a439e99df65-13.jpg?height=149&width=1233&top_left_y=2115&top_left_x=351)
定理 2．5．10．由引理易知 $V_{A B} \subseteq V_{A}$ ，因此

$$
r(A B)=\operatorname{dim} V_{A B} \leqslant \operatorname{dim} V_{A}=r(A) ;
$$

A699列婹量•四为A中列府量延放组公

考感行空间可类似地证明 $r(A B) \leqslant r(B)$ 、亦可由上面证明的结论和秩在转置下不变得到

$$
r(A B)=r\left(B^{\top} A^{\top}\right) \leqslant r\left(B^{\top}\right)=r(B)
$$

性质 2．5．11．设 $A, B \in F^{m \times n}$ ，则 $r(A+B) \leqslant r(A)+r(B)$ ．

$$
\begin{aligned}
& A\left(\alpha_{1}-\alpha_{n}\right) \quad B=\left(\beta_{1} \ldots \beta_{n}\right) \\
& A+B=\left(\alpha_{1}, \beta_{1} \ldots \alpha_{n}, \beta_{n}\right) \\
& \Rightarrow \alpha_{1}, \beta_{1} \in V_{A}, V_{B} \quad \operatorname{dim}\left(V_{A}, B\right)=\operatorname{dim}\left(V_{A} V_{B}\right) \leq \operatorname{dim}\left(V_{A}\right) \cdot \operatorname{dim}\left(V_{B}\right) \\
& \Rightarrow V_{A 1} B \leq V_{A} \cdot V_{B} \quad \therefore \operatorname{dim}(A) \\
& \quad=r(B)
\end{aligned}
$$

性质 2．5．12．设 $A \in F^{n \times n}, B \in F^{p \times q}, C=\left(\begin{array}{cc}A & O_{m \times q} \\ O_{p \times n} & B\end{array}\right)$, 则 $r(C)=r(A)+r(B)$ ．
A D $C\left(C_{1}-C_{n}, d_{1}-d q\right)$

$$
\begin{aligned}
& \eta c=\left(c_{1} \ldots, c_{n}, d_{1} \ldots, d_{q}\right) \quad\left(\begin{array}{ll}
O_{p \times n} & B
\end{array}\right) \text {, } A D \text {, } \\
& \text { 记 } V_{1}=\operatorname{span}\left(0_{1} \ldots c_{n}\right) V_{2}=\operatorname{span}\left(d_{1} \ldots d_{q}\right), \quad C=\left(\begin{array}{ll}
A & D_{B}
\end{array}\right) \\
& \therefore V_{1} \cap V_{2}=\{0\} \Rightarrow \operatorname{dim}\left(V_{1} \cap V_{2}\right)=0 \text { | } \overline{\Gamma(C) \leqslant M(A) \text { गता(B) }} \\
& \text { ∴ } \operatorname{dim} v_{1}+\operatorname{dim} v_{2}=\operatorname{dim} v_{1}+v_{2} \\
& \therefore r(C)=r(A) \cdot r(B) \\
& \text { 记 } A=\left(\alpha_{1} \ldots \alpha_{n}\right) \quad \operatorname{Be}\left(\beta_{1} \ldots \beta_{r}\right) \quad D=\left(d_{1}, \ldots \frac{\sqrt{\operatorname{din} V_{r} \cap V_{2}} \geqslant \theta}{V_{2}=\operatorname{sen}\left\{p_{r}\right\}}\right. \\
& C_{i}=\binom{\alpha_{i}}{0} \quad d_{i} \quad \frac{d i}{a}, d_{i}=\binom{d i}{\theta_{i}} \quad \text { 则 } \operatorname{dim} V_{(0)} \quad C>\left(c_{1}-c_{n}, e_{1} \ldots e_{r}\right) \\
& \text { 取 } B \text { 技大线性无规日, 坛方记为 } \rho_{1} \ldots \beta_{r} \text { 则䍉 } \\
& \text { 且 } \operatorname{Span}\{a\} \cap S_{p a n}\left\{e_{1} \ldots e_{r_{1}}\right\}=\phi \Rightarrow \operatorname{dim}\left(\operatorname{Span}\left\{c_{1}\right\}\right) \operatorname{dim}\left(\operatorname{Spon}\left\{e_{1} . . e_{r}\right\}\right) \\
& =\operatorname{dim}\left(\operatorname{span}\left\{c i, e_{\ldots} . . e r_{1}\right\}\right) \\
& \operatorname{rank} C \rightarrow \operatorname{rank} A, r_{1}=\operatorname{rark} A+\operatorname{rank} B \quad \leq \operatorname{dim}(\operatorname{spand}(i, e j\})
\end{aligned}
$$

性质 2．5．13．设 $A \in F^{m \times n}, B \in F^{n \times p}, C \in F^{p \times q}$ ，则
酰䧸其特Sylvester 不等式：$r(A B) \geqslant r(A)+r(B)-n$ ，
弗罗只尼里舟Frobenius 不等式：$r(A B C) \geqslant r(A B)+r(B C)-r(B)$ ．
$\begin{array}{ll} & S_{A B} \geqslant n-\operatorname{dim} S_{A} n-\operatorname{dim} S_{B}-n \\ \text {（2）：} \approx \operatorname{dim} S_{A B C}, \operatorname{dim} S_{B} & \Leftrightarrow \operatorname{dim} S_{A}+\operatorname{dim} S_{B} \geqslant \operatorname{dim} S_{A B} \\ & S_{A B C} \\ & \text { 对 } \forall y \in S B \text { 有 } B y=0\end{array}$
![](https://cdn.mathpix.com/cropped/9df01145-b796-4202-bf1f-3a439e99df65-14.jpg?height=164&width=1219&top_left_y=2272&top_left_x=214)
\｛ $\left.u_{1} \ldots u_{k} v_{1} \ldots v_{1}\right\}$ 配 组基 $\left\{u_{1}, u_{2} \ldots u_{k}\right\}$ 可更拓为 $S_{A B}$ 基元象 $\left\{u_{1}, u_{2} \ldots u_{k}, v_{1},-v_{2}\right\}$
延手咥故 $\left\{u_{1} \cdots u_{2} v_{1} \cdots v_{2}\right\} \therefore A\left(B v_{i}\right)=0$ 但 $B v_{i} \neq 0 \Rightarrow B v_{i} \leqslant S_{A}$

$$
S_{B C}
$$

C $\left\{u_{1}, u_{2}, v_{2}, v_{2}\right.$ 让：$B v_{i}$ 线性无关，结急 $C_{3} \in F$ ，使 $C_{1} B v_{1} \cdots, C_{L} B V_{L}=0$
再自 46 至 SABC $\left\{\begin{array}{c}w_{1} \ldots w_{t} \\ w_{1} \ldots\end{array}\right\}$
$\therefore \operatorname{dim} S_{A B}-\operatorname{dim} S_{B} \geqslant t=\operatorname{dim} S_{A B C}-\operatorname{dim} S_{B C}$

## 2．6．线性方程组解判定定理

定理 2．6．1．设 $A \in F^{m \times n}, b \in F^{m \times 1}, A_{b}=(A \mid b), x=\left(\begin{array}{c}x_{1} \\ x_{2} \\ \vdots \\ x_{n}\end{array}\right)$ 是由未知量构成的列同量．
（i）$A x=b$ 有解当且仅当 $r(A)=r\left(A_{b}\right)$ ，无解当且仅当 $r(A)<r\left(A_{b}\right)$ ，且无解时必有 $r(A)+1=r\left(A_{b}\right)$ ；
（ii）$A x=b$ 有解时，自由变量个数为 $n-r(A)$ ，故此时 $A x=b$ 有唯一解当且仅当 $r(A)=n$ ，即 $A$ 可逆，且此时唯一解为 $x_{0}=A^{-1} b$ 。

最后我们从线性空间的角度来分析线性方程组解集的结构。
设 $A \in F^{m \times n}, b \in F^{\prime \prime \prime}$ 、称线性方程组 $A x=b$ 的一个解 $x_{0} \in F^{\prime \prime}$ 为一个解向量。若 $b=0$ ．则方程组为济次线性方程组．令

$$
S_{A}=\left\{x \in F^{\prime \prime} \mid A x=0\right\}
$$

$A \&=b$ 有解 00
则 $10^{\circ} 5 A$ 何为 $A x=b$ 解
$A \&=b$ 有解人口
则 $10^{\circ} 5 A$ 何为 $A x=b$ 解

则显然 $S_{A}$ 中的向量关于加法和数乘封闭。因此 $S_{A}$ 是 $F^{n}$ 的子空间。称为齐次线性方程组 $A x=0$ 的解空间，

显然 $\operatorname{dim} S_{A} \leqslant n$ 。解空间 $S_{A}$ 的一组基也称为齐次线性方程组 $A x=0$ 的一组基础解系。那么 $S_{\curlywedge}$ 即为基咄解系的线性组合全体。因此寻找 $A x=0$ 的一组基础解系是我们的主

要任务．事实上，$A x=0$ 的通解有如下形式：

$$
\left\{\begin{aligned}
& x_{j_{1}}=-c_{1 k_{1}} t_{1}-c_{1 k_{2}} t_{3}-\cdots-c_{1 k_{n-r}} t_{n-r} \\
& x_{j_{2}}=-c_{2 k_{1}} t_{1}-c_{2 k_{2}} t_{2}-\cdots-c_{2 k_{n-r}} t_{n-r} \\
& \cdots \cdots \cdots \cdots \cdots \\
& x_{j_{r}}=-c_{r k_{1}} t_{1}-c_{r k_{2}} t_{2}-\cdots-c_{r k_{n-r}} t_{n-r 1} \\
& x_{k_{1}}=t_{1}, \\
& x_{k_{2}}= t_{2}, \\
& \cdots \cdots \cdots \cdots \\
& x_{k_{n-r}}= t_{n-r 1}
\end{aligned}\right.
$$

其中 $t_{1}, t_{2}, \cdots, t_{n-r}$ 为 $F$ 中任意元素。若将上述等式驽 $x_{1}, x_{2}, \cdots, x_{n}$ 的烟序重新排列。则 $A x=0$ 的通解可写为

$$
x=t_{1} \eta_{1}+t_{2} \eta_{2}+\cdots+l_{n-r} \eta_{n-r} .
$$

其中 $\eta_{1}, \eta_{2}, \cdots, \eta_{n-r} \in F^{n}$ ，且有

$$
\left(\begin{array}{llll}
\eta_{1} & \eta_{2} & \cdots & \eta_{n-r}
\end{array}\right) \xrightarrow{\text { 良的兄同 }}\left(\begin{array}{cccc}
-c_{1 k_{1}} & -c_{1 k_{2}} & \cdots & -c_{1 k_{n-r}} \\
-c_{2 k_{1}} & -c_{2 k_{2}} & \cdots & -c_{2 k_{n-r}} \\
\vdots & \vdots & & \vdots \\
-c_{r k_{1}} & -c_{r k_{2}} & \cdots & -c_{r k_{n-r}} \\
1 & 0 & \cdots & 0 \\
0 & 1 & \cdots & 0 \\
\vdots & \vdots & & \vdots \\
0 & 0 & \cdots & 1
\end{array}\right)
$$

容易看出此矩阵列满秩，故 $\eta_{1}, \eta_{2}, \cdots, \eta_{n-r}$ 线性无关。这表明 $\eta_{1}, \eta_{2}, \cdots, \eta_{n-r}$ 即为 $A x=0$的一组基础解系。特别地、解空问 $S_{\lambda}$ 的维数与 $A$ 的秩满足如下关系：

定理 2．6．2．若 $A \in F^{m \times n}$ ，则 $\operatorname{dim} S_{A}=n-r(A)$ ．
接下米我们先回过来证明上节内容中的柣不等式：

邻接矩降：
![](https://cdn.mathpix.com/cropped/9df01145-b796-4202-bf1f-3a439e99df65-16.jpg?height=214&width=248&top_left_y=2133&top_left_x=488)

$$
\begin{aligned}
& \text { → } A=(0 i j) \quad a_{i} j= \begin{cases}1 & v_{i}, v_{j} \text { 最线 } \\
0 \quad \text { 反之 }\end{cases}
\end{aligned}
$$

$=$ 到 $j$ 长复为 2 的路径数
![](https://cdn.mathpix.com/cropped/9df01145-b796-4202-bf1f-3a439e99df65-16.jpg?height=363&width=845&top_left_y=2381&top_left_x=260)

$$
\begin{array}{lll}
1 & 2 & \text { 则可过或? } \\
5 & \cdot 3 & \text { 有 } \Delta: 1 \text { 种 } \\
5=010
\end{array}
$$

例 2．6．1．求解下列线性方程组：

$$
\left\{\begin{array}{l}
x_{1}+3 x_{2}-2 x_{3}+4 x_{1}+x_{3}=2 \\
2 x_{1}+6 x_{2}-3 x_{3}+5 x_{4}+2 x_{5}=5 \\
4 x_{1}+11 x_{2}-7 x_{3}+15 x_{4}+5 x_{6}=3 \\
x_{1}+3 x_{2}-x_{3}+x_{4}+x_{5}=3
\end{array}\right.
$$

对然性方程组、擞矩降 $A=\left(\begin{array}{ccccc}1 & -1 & 3 & -2 & 9 \\ 2 & 6 & -3 & 5 & 2 \\ 4 & 11 & -7 & 15 & 5 \\ 1 & 3 & -1 & 1 & 1\end{array}\right)$

$$
\begin{aligned}
& A_{0}=\left(\begin{array}{cccccc}
1 & 3 & -2 & 4 & 1 & 2 \\
2 & 6 & -3 & 5 & 2 & 5 \\
4 & 11 & -7 & 15 & 5 \\
1 & 3 & -1 & 1 & 1 & 3
\end{array}\right) \rightarrow\left(\begin{array}{cccccc}
1 & 3 & -1 & 1 & 1 & 1 \\
1 & 3 & -2 & 4 & 1 & 2 \\
0 & 4 & 1 & 3 & 0 & -3 \\
0 & -1 & 1 & -1 & 1 & -5 \\
0 & 0 & 1 & -3 & 0 & 1
\end{array}\right) \rightarrow\left(\begin{array}{ccccc}
1 & 0 & 1 & 1 & 4 \\
0 & 1 & -1 & 1 & -1 \\
0 & 0 & 1 & -3 & 0 \\
0 & 0 & 0 & 0 & 0 \\
0
\end{array}\right) \rightarrow \\
& \rightarrow\left(\begin{array}{cccccc}
1 & 0 & 0 & 4 & 4 & -14 \\
0 & 1 & 0 & -2 & -1 & 6 \\
0 & 0 & 1 & -3 & 0 & 1 \\
0 & 0 & 0 & 0 & 0 & 0
\end{array}\right) \begin{array}{l}
r(A)=r\left(A_{b}\right) \Rightarrow A_{a}=b \text { 有解 } \\
\text { 且 } \operatorname{dim} S_{B}=5-r(A)=2
\end{array} \\
&\text {-其出解系: } \left.\left(\begin{array}{ccc}
1 & 0 & 0 \\
0 & 1 & 0
\end{array}\right) \rightarrow-2-1\right)
\end{aligned}
$$

000000 可知有特解 $\gamma=\left(\begin{array}{c}-19 \\ -6 \\ 8\end{array}\right)$ 基6出解系：$\left(\begin{array}{ccc}1 & 0 & 0 \\ 0 & 1 & 2-1 \\ 0 & 0 & 1 \\ -3 & 0\end{array}\right)\left(\begin{array}{l}A_{1} \\ \vdots \\ A_{5}\end{array}\right)=0$

## 2．7．章节附录 I：线性方程组的组合应用

$$
\eta_{1}=\left(\begin{array}{c}
-9 \\
2 \\
3 \\
1 \\
0
\end{array}\right) \quad \eta_{2}=\left(\begin{array}{c}
-9 \\
1 \\
8 \\
0 \\
1
\end{array}\right)
$$

本节内容旨在围绕线性方程组解的判定和解空间维数。解决一些组合问题 $\quad$ AN－b解解 $\left\{r_{1}+r_{1} \eta_{1}+k_{2} \eta_{2}\right\}$
1．将一个 $n \times n$ 表格的方格染成白色和黑色。假设存在一个非空的行集合 $A$ ，使得表格的 $k, k \in Z$每一列都包含偶数个属于 $A$ 的白色方恪。证明存在一个非空的列集合 $B$ ，使得表格的每一行都包含偶数个属于 $B$ 的白色方格。

将表格视为 0,1 的 $n$ 吅方阵，$C=$（ $G$ 守）老第 1 行第）列白白络，则 $C(1)=1$ ，利则为 0
![](https://cdn.mathpix.com/cropped/9df01145-b796-4202-bf1f-3a439e99df65-17.jpg?height=118&width=1327&top_left_y=1719&top_left_x=524)

$$
\begin{aligned}
& \text { 记 } x=\left(\begin{array}{l}
x_{1} \\
1 \\
x_{n}
\end{array}\right) \text { 吾治 } A \text {, 记 } x_{i}=1 \text {, 否则 } x_{i}=0 \\
& \left\{\begin{array}{l}
C_{11} Z_{1}+C_{21} Z_{21} \cdots+C_{n 1} Z_{n}=0 \\
C_{12} Z_{11} \cdots \cdot C_{n 2} Z_{n}=0 \\
\cdots C_{1 n} Z_{1} \cdots+C_{n n} Z_{n}=0
\end{array} \text { 有古㩕解x, } d \operatorname{rank} C^{\top}<n \Rightarrow \operatorname{rank} C^{\sigma}<n \quad \text { 即 } C^{y} \cdot y\right. \text { 有非曹解 } \\
& \left\{\begin{array}{l}
c_{1} y_{1} c_{2} g_{2} \ldots+c_{1} \cap y_{n}=0 \quad \text { 记 } B-\left\{j \mid y_{j}=1\right\} \quad \dot{\xi}_{0}^{n} c_{j j} y_{j}=0 \\
6
\end{array}\right. \\
& \text { 记为 }\binom{y_{1}}{\dot{y}_{n}} \\
& \text { 即家示第行在陃集吉个白格数为偶 }
\end{aligned}
$$

2．设 $n$ 是一个正整数，求最大的正整数 $m$ ，使得存在 $m$ 个不同的集合 $A_{1}, A_{2}, \cdots, A_{m} \subseteq \{1,2, \cdots, n\}$ ，满足对任意 $1 \leq i \leq m l_{1}\left|A_{1}\right|$ 为奇数，且对任意 $1 \leq i<j \leq m,\left|A_{1} \cap A_{j}\right|$ 为個数。

令 $A_{i}=\{1\}$ 滿足条件
又 $=(A \ldots n)^{\top} \quad y=\left(y_{1}-y_{n}\right)^{\top} \quad g_{1}, x(\in\{0,1\}$
$\Delta \circ y=\sum_{i=1}^{n} A_{i} y_{i}$ 定义Ai列向量 $\left(\begin{array}{c}o_{i 1} \\ o_{i 2} \\ \vdots \\ o_{i j}\end{array}\right): a_{1 j}=\left(\begin{array}{ll}1 & j G \Delta i \\ 0 & j \Delta A_{i}\end{array}\right.$
则 $a_{i}, a_{j}$ 内织：$\left(a_{i}, a_{j}\right)=\sum_{i=1}^{n} a_{i k} a_{j k}=\left|\|_{i} \cap A J\right|$

$$
\left(a_{i}, a_{i}\right)=\hat{F}_{i-1} a_{i \mu}^{2}=\left|A_{i}\right|
$$

下证：$\left\{a_{1}\right\}$ 线性无关
散后。 $c_{i} \in\{0,1\}$ ，使 $a_{1} a_{1}+c_{2} a_{2}+\ldots, c_{n} a_{n}=0$
考总 $\forall a_{i}$ 与 $\sum_{k=1}^{n} c_{k} a_{k}$ 作内和

$$
\left(a_{i}, 0\right)=0=\left(a_{i}, c_{k=1}^{\infty} c_{k} a_{k}\right)=\sum_{k=1}^{c} c_{k}\left(a_{i}, a_{1 k}\right)=c_{k} c_{1}
$$

$\mathrm{Ci}_{\text {i }}$ 线性无关

$$
\therefore m \leqslant n
$$

3．在石头其刀布循环锦标赛中，任何两名选手连续对战十次，每名选手部有一个旉愛的策略，这是一个固定的长为 10 的序列（例如，$R R S P P R S P P S$ ），他们用它来对战其他所有
![](https://cdn.mathpix.com/cropped/9df01145-b796-4202-bf1f-3a439e99df65-18.jpg?height=53&width=1301&top_left_y=1512&top_left_x=358) 1024 名迩正参加了䠉俆养。

将 $R$ 、 $R P$ 、 $S$ 视为 $0,1,2$ ，定义胜负关系 $S \rightarrow P \xrightarrow{\text { 鸳 }} R \rightarrow S$

记每策略视为 $a \in\left(\xi_{3}\right)^{10}, a-y=1, a=\left(\begin{array}{c}a_{1} \\ a_{1} \\ a_{1}\end{array}\right)$ ，记 $a$ 集合 $S$

对 $\forall O_{G S}$ ，足义罗项式 $F_{a}\left(\left(A_{1}, A_{2}, a_{0}^{\prime}\right)\right.$对 $\forall a, b \in S, F_{a}(b)=0, F_{a}(c)=1$
F证。 $F_{a}(x)$ 线性气关 $\left(Z_{2}\right.$ 上或 $)$
否则 $\exists C_{0}=\{0,1\}$ 使 $\sum_{0 \in A} C_{a} F_{a}(x)=0$
对省 $r \in S$ ，$a^{2} \in b_{a} f_{0}(r)$

$$
=S_{0} C_{r}=0
$$

－而多项式为 $\mathrm{x}_{1}-\lambda_{10}$ 线性组合构成向量空间

$$
\operatorname{dim} V=2^{10}
$$

∴ 至多有 $2^{10}=1024^{\prime}$ 个元素

## 2002－0 w 已Sr－9

4．设 $n$ 是正監数，$A_{1} 、 A_{2} 、 \cdots 、 A_{n+1}$ 是杹合 $(1,2, \cdots, n)$ 的 $n+1$ 个非空子集，求证：存在 $\{1,2, \cdots, n+1\}$ 的两个不交的非空子集 $\left(i_{1}, i_{2}, \cdots, i_{k}\right)$ 和 $\left\{j_{1}, j_{2}, \cdots, j_{m}\right\}$ ，使得

$$
A_{11} \cup A_{1,} \cup \cdots \cup A_{16}=A_{1,} \cup A_{17} \cup \cdots \cup A_{1 m}
$$

![](https://cdn.mathpix.com/cropped/9df01145-b796-4202-bf1f-3a439e99df65-19.jpg?height=99&width=563&top_left_y=549&top_left_x=722)
⇒ 入㤢正郁负，记 $\mathrm{r}=\left\{1, \ldots \mathrm{i}_{k}\right\}$ 为入 $>0$ 的府示集
日 $J=\{J, \ldots \mathrm{~J}\}\}$ 为 入 0 的际集

$$
\begin{aligned}
& \Rightarrow \sum_{i \in I} \lambda_{i} a_{i}=\sum_{j \omega_{j}}\left(-\lambda_{j}\right) a_{j} \\
& \text { 下证 } A_{1} \cup A_{i_{L}} \cup \ldots \cup A_{i_{K}}=A_{1} \cup A_{2} \cup-\cup A_{j n} \\
& \text { 对 } \forall r \in\{1,2 \ldots n\} \text { 我 } r \in A_{i} \cup A_{i} \cup \ldots A_{i r} \Rightarrow \sum_{r \in 1} \lambda i a_{1 r}>0 \\
& \therefore \sum_{S \in}\left(-A_{j}\right) a_{j r}>0 \Rightarrow r G A_{j 1} U \ldots A_{j m} \Rightarrow A_{11} \cup A_{1}, U_{\ldots} \cup A_{1 \mu} \subseteq A_{1} U_{\ldots} A_{j m} \\
& \text { 同里 AjijAji ...Amc Ailu-UAik } \\
& \therefore A_{1} \cup A_{2} \ldots A_{j m}=A_{i 1} \cup A_{i 2}-A_{i k}
\end{aligned}
$$

5．一位魔术师表演如下的魔术。他先告诉观众一个正整数 $n$ ，以及 $2 n$ 个实数 $x_{1}<x_{2}< \cdots<x_{2 n}$ 。观众秘密地选择一个 $n$ 次实系数费项式 $P(x)$ ，计算 $2 n$ 个值 $P\left(x_{1}\right), P\left(x_{2}\right), \cdots$ ， $P\left(x_{2 n}\right)$ ，并将它们以不减的顺序写在黑板上．之后魔术师说出观众所选的多项式．问：魔术师是否总能成功表㴓？

## 2．8．习题

## Section A

1．求证：数域 $F$ 上不存在 $n$ 阶方阵 $A, B$ 满足 $A B-B, A=I_{n}$ ．

2．分别在有理数域 $\mathbb{Q}$ 和剩余类域 $\mathbb{Z}_{3}$ 上求解下列线性方程组：
（i）$\left\{\begin{array}{l}x_{1}+2 x_{3}-x_{3}=0, \\ 2 x_{1}+x_{2}+x_{3}=0 ;\end{array}\right.$
（ii）$\left\{\begin{array}{l}x_{1}+2 x_{2}-x_{3}=5 . \\ x_{1}+x_{2}+x_{3}=1, \\ 2 x_{1}-2 x_{2}+x_{3}=4 .\end{array}\right.$

3．用初等变换将下列数域 $F$ 上的矩阵化为标准形：
（i）$\left(\begin{array}{lll}3 & 2 & -4 \\ 3 & 2 & -4 \\ 1 & 2 & -1\end{array}\right)$ ；
（ii）$\left(\begin{array}{rrrrr}1 & -1 & 2 & 1 & 0 \\ 2 & -2 & 4 & 3 & 0 \\ 4 & 0 & 7 & 3 & 2\end{array}\right)$ ．

4．证明：和第一类 $n$ 阶初等矩阵 $P_{\mathrm{b}}(i \neq j)$ 均可交换的矩阵 $A$ 可表示为 $A=\lambda I_{n}+\mu E$ 、其中 $E$ 是所有元素均为 1 的 $n$ 阶方阵。

5．设 $A$ 是二阶方阵，且存在正整数 $k \geqslant 2$ 使得 $A^{k}=O$ ．求证：$A^{2}=O$ ．

6．设 $A$ 是二阶方阵且 $A^{2}=I_{2}$ ，但 $A \neq \pm I_{2}$ ．求证：$r\left(A+I_{2}\right)=r\left(A-I_{2}\right)=1$ ．

7．求证：若 $A \in F^{n \times n}$ 覀等即 $A^{2}=A$ ，则 $r(A)+r\left(I_{n}-A\right)=n$ ．

8．对任意域 $F$ ，求证：$F^{n \times n}$ 中任意一个矩阵均可表示为一个可逆矩阵和一个程等矩阵（即满足 $A^{2}=A$ 的矩阵 $\left.A\right)$ 之积。

9．设 $A \in F^{m \times n}$ 的佚 $r>0$ ．求证：
（i）存在非零向量 $\alpha_{1}, \alpha_{2}, \cdots, \alpha_{r} \in F^{1 n \times 1}$ 和 $\beta_{1}, \beta_{2}, \cdots, \beta_{r} \in F^{1 \times n}$ ，使得

$$
A=\alpha_{1} \beta_{1}+\alpha_{2} \beta_{2}+\cdots+\alpha_{r} \beta_{r}
$$

（ii）若（i）中等式成立，则向量组 $\alpha_{1}, \alpha_{2}, \cdots, \alpha_{r}$ 和 $\beta_{1}, \beta_{2}, \cdots, \beta_{r}$ 分别线性无关．

10．设线性空间 $V$ 中向量组 $\left(\alpha_{1}, \alpha_{2}, \cdots, \alpha_{m}\right\},\left\{\beta_{1}, \beta_{2}, \cdots, \beta_{m}\right\}$ 和 $\left\{\alpha_{1}+\beta_{1}, \alpha_{2}+\beta_{2}, \cdots, \alpha_{1 n}+\beta_{1 n}\right\}$的秩分别为 $r_{1}, r_{2}$ 和 $r_{3}$ 。求证：$r_{1} \leqslant r_{2}+r_{3}, r_{2} \leqslant r_{1}+r_{3}, r_{3} \leqslant r_{1}+r_{2}$ ．

11．设 $F$ 为 $q$ 元有限域．
（i）求 $\mathrm{GL}_{n}(F)$ 的阶（即这个群所含的元索个数）；
（II）对 $0 \leqslant m \leqslant n$ ，定义 Grassmaun 集 $G_{n, m}$ 为 $F^{n}$ 的 $m$ 维子空间之集。式计算 $\left|G_{n, m}\right|$ ．

12．设 $A=\left(a_{1}\right)_{n \times n}$ 为 $n$ 阶复方阵，求证：严植对角占伦
（I）若对任意 $i=1,2, \cdots, n$ ，有 $2\left|a_{11}\right|>\sum_{j=1}^{n}\left|a_{i j}\right|$ 。则 $A$ 可逆：
（il）若对任意 $l, j=1,2, \cdots, n, i \neq j$ ，有 $\left|a_{i i}\right|>(n-1)\left|a_{j}\right|$ ，则 $A$ 可逆．

13．对下列方程组用其导出组的基础解系表示其通解：

$$
\left\{\begin{array}{l}
x_{1}+3 x_{2}+5 x_{3}-4 x_{4}=1 \\
x_{1}+3 x_{2}+2 x_{3}-2 x_{4}+x_{5}=-1 \\
x_{1}-2 x_{2}+x_{3}-x_{4}-x_{5}=3 \\
x_{1}-4 x_{2}+x_{3}+x_{4}-x_{5}=3 \\
x_{1}+2 x_{2}+x_{3}-x_{4}+x_{5}=-1
\end{array}\right.
$$

14．解 $\mathbb{R}$ 上齐次线性方程组

$$
\left\{\begin{array}{c}
x_{2}+x_{3}+x_{4}+\cdots+x_{n-1}+x_{n}=0 \\
x_{1}+x_{3}+x_{1}+\cdots+x_{n-1}+x_{n}=0 \\
x_{1}+x_{2}+x_{1}+\cdots+x_{n-1}+x_{n}=0 \\
\cdots \cdots \cdots \cdots \\
x_{1}+x_{2}+x_{3}+\cdots+x_{n-2}+x_{n-1}=0
\end{array}\right.
$$

其中 $n>1$ ．

## Section B：旨在认识和补充一些概念和结构

15．方阵 $A \in F^{n \times n}$ 的对角线元索之和称为 $A$ 的迹，并记作 $\operatorname{tr}(A)$ ．设 $A, B \in F^{n \times n}$ ．求证：
（i） $\operatorname{tr}(A+B)=\operatorname{tr}(A)+\operatorname{tr}(B)$ ；
（ii） $\operatorname{tr}(\lambda A)=\lambda \operatorname{tr}(A), \lambda \in F$ ；
（iii） $\operatorname{tr}(A B)=\operatorname{tr}(B A)$ ．

16．若 $A^{\top}=A$ ，则称 $A$ 为对称矩阵．求证：若 $A$ 为实对称矩阵且 $A^{2}=O$ ，则 $A=O$ ．

17．设 $A, B \in F^{n \times n}$ ，求证：
（1）若 $A, B$ 对称，则 $A B$ 对称当且仅当 $A B=B A$ ；
（Ii）若 $A^{2}=B^{2}=I_{n}$ ，则 $(A B)^{2}=I_{n}$ 当且仅当 $A B=B A$ ；
（iii）若 $A, B$ 箱等（即 $A^{2}=A 、 B^{2}=B$ ），则 $(A+B)^{2}=A+B$ 当且仅当 $A B+B A=O$ ．

18．若 $A^{\top}=-A$ ，则称 $A$ 为反对称矩阵．设 $A . B$ 是反对称矩阵．求证：
（I）$\Lambda^{2}$ 是对称矩阵：
（II）$A B-B A$ 是反对称矩阵；
（iii）$A B$ 对标当且仅当 $A B=B A$ ．

10．矩阵 $\left(a_{i j}\right)_{n \times n} \in F^{n \times n}$ 称为上三角形矩阵，如果对任意 $1 \leqslant j<i \leqslant n$ 有 $a_{i j}=0$ 。记 $b_{n}(F)$ 为 $F^{n \times n}$ 中上三角形矩阵之集，证明 $b_{n}(F)$ 是 $F^{n \times n}$ 的子环。类似地，可以定义下三角形矩阵并对其得到同样的结论。

20．（Hamilton（哈密顿）四元数）令 $\mathbb{H}=\left\{\left.\left(\begin{array}{cc}a & b \\ -\bar{b} & \bar{a}\end{array}\right) \right\rvert\, a, b \in \mathbb{C}\right\}$ ．
（i）证明 $\mathbb{H}^{-1}$ 是 $\mathbb{C}^{2 \times 2}$ 的子环，并且是一个除环（即非雾元均可逆）：
（ii）在 $\mathbb{H}$ 中解方程 $x^{2}+1=0$ ，这里 1 指单位元 $I_{2}$ ．

21．（二阶特殊线性羘）将矩阵元素为整数的二阶方阵之集记为 $\mathbb{Z}^{2 \times 2}$ 。令

$$
\operatorname{SL}_{2}(\mathbb{Z})=\left\{\left.\left(\begin{array}{ll}
a & b \\
c & d
\end{array}\right) \in \mathbb{Z}^{2 \times 2} \right\rvert\, a d-b c=1\right\} .
$$

（i）证明 $\mathrm{SL}_{2}(\mathbb{Z})$ 对矩阵乘法构成一个群；
（ii）对正整数 $N$ ，令 $\Gamma_{0}(N)$ 为 $\mathrm{SL}_{2}(\mathbb{Z})$ 中满足 $N \mid c$ 的矩阵 $\left(\begin{array}{ll}a & b \\ c & d\end{array}\right)$ 之集，证明 $\Gamma_{0}(N)$是 $\mathrm{SL}_{2}(\mathbb{Z})$ 的子群；
（ii）对正整数 $N$ ，令 $\Gamma(N)$ 为 $\mathrm{SL}_{2}(\mathbb{Z})$ 中满足 $N \mid a-1, d-1, b, c$ 的矩阵 $\left(\begin{array}{ll}a & b \\ c & d\end{array}\right)$ 之集，证明 $\Gamma(N)$ 是 $\Gamma_{0}(N)$ 的子群。

22．实方阵 $A=\left(a_{i j}\right)_{n \times n} \in \mathbb{R}^{2 \times n}$ 称为 Markov 矩阵，如果 $a_{i j} \geqslant 0 . \sum_{i=1}^{n} a_{i j}=1, j= 1,2, \cdots, n$ 。实向量 $x=\left(x_{1}, x_{2}, \cdots, x_{n}\right)^{\top} \in \mathbb{R}^{n \times 1}$ 称为概率向量，如果 $x_{i} \geqslant 0, \sum_{i=1}^{n} x_{i}=1$ 。 Markov 矩阵在实际应用中非常重要。求证：
（i）$A \in \mathbb{R}^{n \times n}$ 是 Markov 矩阵当且仅当对任意概率向量 $r \in \mathbb{R}^{n \times 1}, A x$ 仍为概率向量；
（ii）若 $A, B \in \mathbb{R}^{n \times n}$ 均为 Markov 矩阵，则 $A B$ 也是 Markov 矩阵。特别地、 $A^{k}(k \geqslant 1)$是 Markov 矩阵。

## Section C：请于学习完成行列式章节后再考虑本部分习题

23．在一个有固定的有限个用户的杜交网络里、每个用户拥有由其他用户组成的固定的追随者集合。每个用户拥有一个最初的正整数等级（不一定所有用户都相同）。每天午夜，每位用户的等级变为在该时刻前他的等级以及他的所有追随者的等级之和。设 $m$ 是正整数，一

位不同于该同挤的黑客凉渠使得所有用户的等级都能餏 $m$ 霯除。每一天、他或者将一位用户的等级加1、或者什么也不做、求证：经过若干天后，该黑客可以实现他的目的。

24．设 $G(V, E)$ 是连通的简单图。称 $E$ 的子集 $H$ 为 $G$ 的＂偶度子图＂，如果对任何 $x \in V, H$ 中共有偶数条边以。 $r$ 为顶点。设 $|V|=v,|E|=e$ 、问：$G$ 有奖少个＂偶度子图＂。 （特别地，0也是一个＂偶度子图＂。）

25．一个城镇里有 $n$ 个居民，他们都是一些俱乐部的会员（一个居民可以是不只一个俱乐部的会员）。已知对任意若干个俱乐部。总存在一个居民是其中奇数个的会员，求证：俱乐部的个数不超过 $n$ 。

26．某班级有 $n$ 位同学，共参加了 $m$ 个社团．任意两个社团成员不全相同，且共同学生的数目恰好都是 $k$ 个 $(k>0)$ ．试证明．$m \leq n$ ．

27．平面上有 666 个点，它们不能被 10 条直线䐝盖．求证：可以从中选出 66 个点，它们仍然不能被 10 条直线置盖。

28．一名游客来到一坐小岛、岛上居住着 100 个巫师，每人都是骑士或是骗子。游宮知道当他到达小岛时，恰有一个巫师是犄士，但不知道是谁。在一次操作中，游客可以任意选择两个巫师 $A$ ，$B$ 井让 $A$ 对 $B$ 下咒，若 $A$ 是骑士，则 $B$ 改变身份；若 $A$ 是骗子、则 $B$ 不改变身份。求最大的正整数 $k$ ，使得游客可以通过若干次操作、至少确定 $k$ 个巫师此时的身份．

29．给定正整数 $n$ ．称集合 $S$ 是 $n$－可行的．如果：
－$S$ 的每个元染都是 $\{1,2, \cdots, n\}$ 的三元子集，
－$|\mathcal{S}|=n-2$ ，
－对任意 $1 \leq k \leq n-2$ 以及任意互不相同的 $A_{1}, A_{2}, \cdots, A_{k} \in S$ ，都有

$$
\left|A_{1} \cup A_{2} \cup \cdots \cup A_{k}\right| \geq k+2
$$

问：是否对任意整数 $n>3$ 以及任意 $n$－可行的集合 $\mathcal{S}$ ，平面上总存在互不相同的点 $P_{1}, P_{2} \cdots, ~ P_{n}$ ，使得对任意 $\{i, j, k\} \in S$ ，均有 $\Delta P_{i} P_{j} P_{k}$ 的每个内角都小于 $61^{\circ} ?$

30．已知整数 $n \geq 3$ ，实数 $a_{1}, a_{2}, \cdots, a_{n}>0$ ．若实数 $b_{1}, b_{2}, \cdots, b_{n}$ 满足：对任意 $k \in \{1,2, \cdots, n\}$ ，均有

$$
\sum_{\substack{i, j \in(1,2 \cdots, n) \backslash(k) \\ i \neq j}} a_{1} b_{j}=0 .
$$

求证：$b_{1}=b_{2}=\cdots=b_{n}=0$ ．

31．设 $G$ 是一个有限简单图，对 $G$ 的每个顶点赋以一个非负整数。使得对任意顶点 $v, v$

赋的数等干与 $v$ 相邻的点中貹有偶数的点的个数。求证：满足要求的赋值方法是 2 的宸。

32．某次数学家大会，每两个数学家都互为朋友或者阳生人。会场提供两个富厅，用算的时候、每位数学家都坚持要和偶数个朋友在同一个聟厅、求证：符合所有数学家要求的就答位置的安排方法数是 2 的飛。

