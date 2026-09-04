为了求奇数阶导数在原点的值，我们设法建立递推公式．等式

$$
y \cdot \cos x=\sin x
$$

两边求 $(2 n+1)$ 阶导数，并利用莱布尼茨公式，得

$$
\sum_{k=0}^{2 n+1} \mathrm{C}_{2 n+1}^{k} y^{(2 n+1-k)} \cos \left(x+\frac{k \pi}{2}\right)=\sin \left(x+\frac{2 n+1}{2} \pi\right)
$$

将 $x=0$ 代入上式，即得递推公式

$$
\sum_{k=0}^{2 n+1} \mathrm{C}_{2 n+1}^{k} y^{(2 n+1-k)}(0) \cos \frac{k \pi}{2}=(-1)^{n}
$$

即

$$
\begin{aligned}
y^{(2 n+1)}(0) & -\mathrm{C}_{2 n+1}^{2} y^{(2 n-1)}(0)+\mathrm{C}_{2 n+1}^{4} y^{(2 n-3)}(0)+\cdots \\
& +(-1)^{n-1} \mathrm{C}_{2 n+1}^{2 n-2} y^{(3)}(0)+(-1)^{n} \mathrm{C}_{2 n+1}^{2 n} y^{\prime}(0)=(-1)^{n}
\end{aligned}
$$

将 $n=1$ 及 $y^{\prime}(0)=1$ 代入上式，得

$$
y^{(3)}(0)-\frac{3 \cdot 2}{2!}=-1,
$$

解出 $y^{(3)}(0)=2$ 。
在递推公式中令 $n=2$ ，得

$$
y^{(5)}(0)-\frac{5 \cdot 4}{2!} \cdot 2+\frac{5 \cdot 4 \cdot 3 \cdot 2}{4!} \cdot 1=1
$$

解出 $y^{(5)}(0)=16$ ．
再在递推公式中令 $n=3$ ，得

$$
y^{(7)}(0)-\frac{7 \cdot 6}{2!} \cdot 16+\frac{7 \cdot 6 \cdot 5 \cdot 4}{4!} \cdot 2-\frac{7!}{6!} \cdot 1=-1
$$

解出 $y^{(7)}(0)=272$ ．
附带有

$$
\begin{array}{cc}
\frac{y^{\prime}(0)}{1!}=1, & \frac{y^{(3)}(0)}{3!}=\frac{1}{3}, \\
\frac{y^{(5)}(0)}{5!}=\frac{2}{15}, & \frac{y^{(7)}(0)}{7!}=\frac{17}{315} .
\end{array}
$$

## 4．4．3 一般函数的高阶导数

对于一般没有什么特性的函数，通常很难归纳出其 $n$ 阶导数的公式，只能逐次地求导．特别对于复合函数、反函数、隐函数和参数式函数求高阶导数更是如此。

例 4．4．8 设函数 $y=y(x)$ 是由方程 $\frac{x^{2}}{a^{2}}+\frac{y^{2}}{b^{2}}=1(y>0)$ 给出，试求 $y^{\prime \prime}$ 。

解法 1 先从方程解出

$$
y=\frac{b}{a} \sqrt{a^{2}-x^{2}}
$$

然后直接计算，可得

$$
\begin{aligned}
y^{\prime} & =-\frac{b}{a}\left(a^{2}-x^{2}\right)^{-\frac{1}{2}} x=-\frac{b x}{a \sqrt{a^{2}-x^{2}}} \\
y^{\prime \prime} & =-\frac{b}{a}\left(a^{2}-x^{2}\right)^{-\frac{3}{2}} x^{2}-\frac{b}{a}\left(a^{2}-x^{2}\right)^{-\frac{1}{2}} \\
& =-\frac{b^{4}}{a^{2} y^{3}}
\end{aligned}
$$

解法2 先将方程表示为参数式

$$
\left\{\begin{array}{l}
x=a \cos t \\
y=b \sin t
\end{array}\right.
$$

再直接计算，有

$$
\begin{aligned}
y_{x}^{\prime} & =-\frac{b \cos t}{a \sin t}=-\frac{b}{a} \cot t \\
y_{x x}^{\prime \prime} & =-\frac{\mathrm{d}\left(y_{x}^{\prime}\right)}{\mathrm{d} x}=-\frac{b}{a} \frac{(\cot t)^{\prime}}{x_{t}^{\prime}} \\
& =\frac{b}{a \sin ^{2} t} \cdot \frac{-1}{a \sin t}=\frac{-b}{a^{2} \sin ^{3} t} \\
& =-\frac{b^{4}}{a^{2} y^{3}}
\end{aligned}
$$

解法 3 直接对方程两边关于 $x$ 求导数，得

$$
\frac{2 x}{a^{2}}+\frac{2 y y^{\prime}}{b^{2}}=0
$$

由此解得

$$
y^{\prime}=-\frac{b^{2} x}{a^{2} y}
$$

再对方程（4．4．2）两边关于 $x$ 求导数，得

$$
\frac{1}{a^{2}}+\frac{y^{\prime 2}+y y^{\prime \prime}}{b^{2}}=0 .
$$

由此解出 $y^{\prime \prime}$ 并且将 $y^{\prime}$ 的表达式代入，得

$$
y^{\prime \prime}=-\frac{b^{4}}{a^{2} y^{3}}
$$

比较上面的三种解法，不难发现，似乎隐函数求导法要简单一些．

## 4．4．4 高阶微分

若函数 $f(x)$ 在区间 $(a, b)$ 内可微，则有 $\mathrm{d} f=f^{\prime}(x) \mathrm{d} x$ ，其中 $f^{\prime}(x)$是 $x$ 的函数，而 $\mathrm{d} x$ 是与 $x$ 无关的一个量。这样，把一阶微分 $\mathrm{d} f$ 看成 $x$的函数，再求一次微分 $\mathrm{d}(\mathrm{d} f)$ ，就称之为 $f(x)$ 的二阶微分，记为 $\mathrm{d}^{2} f$ ，即

$$
\mathrm{d}^{2} f=\mathrm{d}(\mathrm{~d} f)=\left(f^{\prime}(x) \mathrm{d} x\right)^{\prime} \mathrm{d} x=f^{\prime \prime}(x) \mathrm{d} x \mathrm{~d} x=f^{\prime \prime}(x) \mathrm{d} x^{2}
$$

其中 $\mathrm{d} x^{2}=\mathrm{d} x \mathrm{~d} x$（读者应注意 $\mathrm{d} x^{2} \neq \mathrm{d}\left(x^{2}\right)=2 x \mathrm{~d} x$ ）．
一般地，我们定义 $n$ 阶微分为

$$
\begin{aligned}
\mathrm{d}^{n} f & =\mathrm{d}\left(\mathrm{~d}^{n-1} f\right)=\left(f^{(n-1)}(x) \mathrm{d} x^{n-1}\right)^{\prime} \mathrm{d} x \\
& =f^{(n)}(x) \mathrm{d} x^{n-1} \mathrm{~d} x=f^{(n)}(x) \mathrm{d} x^{n}
\end{aligned}
$$

由此即可知道，为什么将 $f^{(n)}(x)$ 记为 $\frac{\mathrm{d}^{n} f}{\mathrm{~d} x^{n}}$ 的缘故了。
由定义可知，求高阶微分本质上就是求高阶导数，因此我们不再详细讨论，但值得指出的是高阶微分是不具有形式不变性的。

事实上，设 $y=f(u)$ ，当 $u$ 为自变量时，我们有 $\mathrm{d}^{2} y=f^{\prime \prime}(u) \mathrm{d} u^{2}$ ；但当 $u=g(x)$ 是 $x$ 的函数时，则

$$
\begin{aligned}
\mathrm{d} f & =f^{\prime}(u) g^{\prime}(x) \mathrm{d} x \\
\mathrm{~d}^{2} f & =f^{\prime \prime}(u)\left(g^{\prime}(x)\right)^{2} \mathrm{~d} x^{2}+f^{\prime}(u) g^{\prime \prime}(x) \mathrm{d} x^{2}=f^{\prime \prime}(u) \mathrm{d} u^{2}+f^{\prime}(u) \mathrm{d}^{2} u
\end{aligned}
$$

这比 $u$ 是自变量时多了一项 $f^{\prime}(u) \mathrm{d}^{2} u!$ 因此，当 $\mathrm{d}^{2} u \neq 0$ ，即 $g^{\prime \prime}(x) \neq 0$时，二阶微分就不具有形式不变性了。

## 习 题 四

1．讨论函数 $f(x)$ 和 $g(x)$ 在 $x=0$ 处的可导性，其中

$$
\begin{aligned}
& f(x)= \begin{cases}-x, & x \in \mathbb{R} \backslash \mathbb{Q}, \\
x, & x \in \mathbb{Q} ;\end{cases} \\
& g(x)= \begin{cases}-x^{2}, & x \in \mathbb{R} \backslash \mathbb{Q}, \\
x^{2}, & x \in \mathbb{Q} .\end{cases}
\end{aligned}
$$

2．设函数 $f(x)$ 在 $(a, b)$ 上有定义，且在点 $x_{0} \in(a, b)$ 处可导，并假定序列 $\left\{x_{n}\right\}$ 和 $\left\{y_{n}\right\}$ 满足

$$
a<x_{n}<x_{0}<y_{n}<b, \quad n=1,2, \cdots,
$$

且有

$$
\lim _{n \rightarrow \infty} x_{n}=x_{0}=\lim _{n \rightarrow \infty} y_{n} .
$$

证明：

$$
\lim _{n \rightarrow \infty} \frac{f\left(y_{n}\right)-f\left(x_{n}\right)}{y_{n}-x_{n}}=f^{\prime}\left(x_{0}\right) .
$$

3．设函数 $f(x)$ 在 $(-1,1)$ 上可导，且满足 $|f(x)| \leqslant|\sin x|$ ，求证 $\left|f^{\prime}(0)\right| \leqslant 1$ ．

4．求证：
（1）可导的偶函数，其导数为奇函数；
（2）可导的奇函数，其导数为偶函数；
（3）可导的周期函数，其导数为相同周期的周期函数．
5．讨论下列函数在指定点 $x_{0}$ 处的可导性：
（1）$y=\left|(x-1)(x-2)^{2}(x-3)^{3}\right|, x_{0}=1,2,3$ ；
（2）$y=\left\{\begin{array}{ll}\frac{1}{4}(x-1)(x+1)^{2}, & |x| \leqslant 1, \\ |x|-1, & |x|>1,\end{array} \quad x_{0}=-1,0,1\right.$ ．
6．设函数 $f(x)$ 在 $[-1,1]$ 上有定义，$f^{\prime}(0)$ 存在，求

$$
\lim _{n \rightarrow \infty}\left[f\left(\frac{1}{n^{2}}\right)+f\left(\frac{2}{n^{2}}\right)+\cdots+f\left(\frac{n}{n^{2}}\right)-n f(0)\right] .
$$

7．求下列序列的极限：
（1） $\lim _{n \rightarrow \infty}\left[\sin \frac{1}{n^{2}}+\sin \frac{2}{n^{2}}+\cdots+\sin \frac{n}{n^{2}}\right]$ ．
（2） $\lim _{n \rightarrow \infty}\left(1+\frac{1}{n^{2}}\right)\left(1+\frac{2}{n^{2}}\right) \ldots\left(1+\frac{n}{n^{2}}\right)$ ．
8．设 $P_{n}(x)$ 为最高次系数为 1 的 $n(n \geqslant 1)$ 次多项式，$M$ 为 $P_{n}(x)=$ 0 的最大实根，求证 $P_{n}^{\prime}(M) \geqslant 0$ ．

9．设函数 $\varphi$ 在点 $a$ 连续，$f(x)=|x-a| \varphi(x)$ ．求 $f$ 在点 $a$ 的左右导数，问在什么条件下 $f(x)$ 在点 $a$ 可导？

10．给定曲线 $y=x^{2}+5 x+4$ ．
（1）确定 $b$ ，使直线 $y=3 x+b$ 为该曲线的切线；
（2）确定 $m$ ，使直线 $y=m x$ 为该曲线的切线．
11．给定曲线 $y=x^{3}$ 和直线 $y=p x-q$ ，其中 $p, q$ 为实数，且 $p>0$ 。
（1）$p$ 给定后，试确定 $q$ ，使 $y=p x-q$ 是 $y=x^{3}$ 的切线；
（2）利用图形求出方程 $x^{3}-p x+q=0$ 有三个不同实根的条件．
12．（1）确定 $m$ ，使 $y=m x$ 为曲线 $y=\ln x$ 的切线；
（2）利用图形求出方程 $\ln x-m x=0$ 有两个实根的条件．

13．问抛物线 $y=x^{2}-2 x-1$ 在哪一点的切线垂直于直线 $x+2 y- 1=0$ ？

14．求下列函数的导数：
（1）$y=\sqrt{x}+\frac{1}{x}-2 x^{3}$ ；
（2）$y=x^{2} 2^{x}$ ；
（3）$y=\frac{2 x-x^{2}}{1+x+x^{2}}$ ；
（4）$y=\frac{x \sin x+\cos x}{x \sin x-\cos x}$ ；
（5）$y=\frac{1}{\sqrt[3]{x}}+\sqrt[3]{x}$ ；
（6）$y=\frac{1}{1+\sqrt{x}}-\frac{1}{1-\sqrt{x}}$ ；
（7）$y=x^{3} \ln x-\frac{1}{n} x^{n}$ ；
（8）$y=\left(x+\frac{1}{x}\right) \ln x$ ；
（9）$y=\sec x$ ；
（10）$y=\frac{\cos x}{x^{4}} \ln \frac{1}{x}$ ．

15．确定常数 $a, b$ ，使函数 $f(x)=\left\{\begin{array}{ll}a x+b, & x>1, \\ x^{2}, & x \leqslant 1\end{array}\right.$ 有连续的导函数．

16．求下列函数的导数：
（1）$y=x\left(a^{2}+x^{2}\right) \sqrt{a^{2}-x^{2}}$ ；
（2）$y=\sqrt[3]{\frac{1+x^{3}}{1-x^{3}}}$ ；
（3）$y=\ln (\ln x)$ ；
（4）$y=\frac{1}{2 a} \ln \left|\frac{a+x}{a-x}\right|$ ；
（5）$y=\ln \left(x+\sqrt{a+x^{2}}\right)$ ；
（6）$y=\ln \tan \frac{x}{2}$ ；
（7）$y=\cos ^{3} x-\cos 3 x$ ；
（8）$y=\sin ^{n} x \cos n x$ ；
（9）$y=\cos (\cos \sqrt{x})$ ；
（10）$y=\frac{\sin ^{2} x}{\sin x^{2}}$ ；
（11）$y=\left(\mathrm{e}^{x}+\mathrm{e}^{-x}\right)^{2}$ ；
（12）$y=\ln \sqrt{\frac{1+\cos x}{1-\cos x}}$ ．

17．设在 $x=1$ 处有 $\frac{\mathrm{d}}{\mathrm{d} x} f\left(x^{2}\right)=\frac{\mathrm{d}}{\mathrm{d} x} f^{2}(x)$ ，求证 $f^{\prime}(1)=0$ 或 $f(1)=1$ ．
18．给定可导函数 $f(x)>0$ ，证明函数 $y=f(x) \sin a x(a>0)$ 永远夹在两条振幅曲线 $y= \pm f(x)$ 之间，并与之相切．

19．求下列函数的导数：
（1）$y=x^{x}(x>0)$ ；
（2）$y=x^{\tan x}(x>0)$ ；
（3）$y=x^{\ln x}(x>0)$ ；
（4）$y=(1+x)^{\frac{1}{x}}(x>0)$ ；
（5）$y=x^{x^{x}}(x>0)$ ；
（6）$y=\mathrm{e}^{-\frac{\sin x}{x^{2}}}(x \neq 0)$ ．

20．求下列函数的导数：
（1）$y=\arcsin \sqrt{1-x^{2}}$ ；
（2）$y=\arccos (\sin x)$ ；
（3）$y=x \arctan x-\frac{1}{2} \ln \left(1+x^{2}\right)$ ；
（4）$y=\arctan \frac{\sqrt{1-x^{2}}-1}{x}+\arctan \frac{2 x}{1-x^{2}}$ ；
（5）$y=\arctan \left(\tan ^{2} x\right)$ ；
（6）$y=\left(\frac{a}{b}\right)^{x}\left(\frac{b}{x}\right)^{a}\left(\frac{x}{a}\right)^{b}(a, b>0)$ ；
（7）$y=\ln \left(\arccos \frac{1}{\sqrt{x}}\right)$ ；
（8）$y=\ln \left(\mathrm{e}^{x}+\sqrt{1+\mathrm{e}^{2 x}}\right)$ ；
（9）$y=\frac{x}{2} \sqrt{a^{2}-x^{2}}+\frac{a^{2}}{2} \arctan \frac{x}{a}(a>0)$ ；
（10）$y=\frac{1}{4 \sqrt{2}} \ln \frac{x^{2}+\sqrt{2} x+1}{x^{2}-\sqrt{2} x+1}-\frac{1}{2 \sqrt{2}} \arctan \frac{\sqrt{2} x}{x^{2}-1}$ ．

21．求证：

$$
\lim _{x \rightarrow a} \frac{f(x)-b}{x-a}=A
$$

的充分必要条件是

$$
\lim _{x \rightarrow a} \frac{\mathrm{e}^{f(x)}-\mathrm{e}^{b}}{x-a}=\mathrm{e}^{b} A
$$

22．设曲线的参数表示：$x=a \cos ^{3} t, \quad y=a \sin ^{3} t \quad(a>0)$ ．
（1）求 $y^{\prime}(x)$ ；
（2）证明曲线的切线被坐标轴所截的长度为一常数．
23．求椭圆 $\frac{x^{2}}{a^{2}}+\frac{y^{2}}{b^{2}}=1$ 在第一象限中的切线，使它平行于过 $(0, b)$和 $(a, 0)$ 的直线．

24．证明曲线

$$
\left\{\begin{array}{l}
x=a(\cos t+t \sin t), \\
y=a(\sin t-t \cos t)
\end{array} \quad(a>0)\right.
$$

上任一点的法线到原点的距离等于 $a$ ．
25．证明圆 $r=2 a \sin \theta(a>0)$ 的向径与切线间的夹角等于极角．
26．设函数 $x(t), y(t)$ 可微，又设 $r=\sqrt{x^{2}+y^{2}}, \theta=\arctan \frac{y}{x}$ 。试求 $\mathrm{d} r, \mathrm{~d} \theta$ 。

27．求下列二元方程表示的函数的导数 $\frac{\mathrm{d} y}{\mathrm{~d} x}$ ：
（1）$x^{3}+y^{3}-x y=0$ ；
（2） $\arctan \frac{y}{x}=\ln \sqrt{x^{2}+y^{2}}$ ；
（3） $\sin x+\cos ^{2} y=1 / 2$ ；
（4）$\left(x^{2}+y^{2}\right)^{2}=x^{2}-y^{2}$ ．

28．定义双曲函数

$$
\begin{array}{ll}
\sinh x=\frac{\mathrm{e}^{x}-\mathrm{e}^{-x}}{2}, & \cosh x=\frac{\mathrm{e}^{x}+\mathrm{e}^{-x}}{2} ; \\
\tanh x=\frac{\sinh x}{\cosh x}, & \operatorname{coth} x=\frac{1}{\tanh x}(x \neq 0) .
\end{array}
$$

再记 $\operatorname{arcsinh} x, \operatorname{arccosh} x, \operatorname{arctanh} x$ 和 $\operatorname{arccoth} x$ 分别为 $\sinh x, \cosh x$ ， $\tanh x$ 和 $\operatorname{coth} x$ 的反函数．
（1）求证：

$$
\begin{array}{ll}
(\sinh x)^{\prime}=\cosh x, & (\cosh x)^{\prime}=\sinh x \\
(\tanh x)^{\prime}=\frac{1}{\cosh ^{2} x}, & (\operatorname{coth} x)^{\prime}=-\frac{1}{\sinh ^{2} x}
\end{array}
$$

（2）用反函数的求导法则计算：

$$
\begin{array}{ll}
(\operatorname{arcsinh} x)^{\prime}, & (\operatorname{arccosh} x)^{\prime}, \quad(\operatorname{arctanh} x)^{\prime}, \\
(\operatorname{arccoth} x)^{\prime}, & (\operatorname{arcsinh}(\tanh x))^{\prime} .
\end{array}
$$

29．设曲线 $\frac{x^{2}}{a^{2}}+\frac{y^{2}}{b^{2}}=1$ 与 $x y=\lambda$ 相切，其中 $a>0, b>0$ 给定．求 $\lambda$ 的值并确定切线方程．

30．证明曲线 $y=a^{x}(a>0, a \neq 1)$ 的任一切线上从切点到与 $x$轴的交点之间的线段在 $x$ 轴上的投影为一常数。

31．求下列函数的微分：
（1）$y=x^{2}-\cos 2 x$ ；
（2）$y=\mathrm{e}^{a x} \sin b x$ ；
（3）$y=\ln \left|\tan \left(\frac{\pi}{4}+\frac{x}{2}\right)\right|$ ；
（4）$y=x^{\sin x^{2}}$ ；
（5）$y=\arctan \mathrm{e}^{x}$ ；
（6）$y=\arcsin \sqrt{1-x^{2}}$ ．

32．设 $u, v$ 是 $x$ 的可微函数，对下列的函数求 $\mathrm{d} y$ ：
（1）$y=\arctan \frac{u}{v}$ ；
（2）$y=\ln \sqrt{u^{2}+v^{2}}$ ；
（3）$y=\ln |\sin (u+v)|$ ；
（4）$y=\frac{1}{\sqrt{u^{2}+v^{2}}}$ ．

33．利用微分求近似值：
（1）$\sqrt[3]{1.02}$ ；
（2） $\arcsin 0.49$ ；
（3） $\mathrm{e}^{0.01}$ ；
（4） $\ln 1.06$ ；
（5）$\sqrt[5]{245}$ ；
（6）$\sqrt{120}$ ．

34．单摆的周期公式是

$$
T=2 \pi \sqrt{\frac{l}{g}},
$$

其中 $l$ 为摆长（单位： cm ），$g=980 \mathrm{~cm} / \mathrm{s}^{2}$ 为重力加速度．设钟摆的周期为 1 s ，在冬季摆长缩短了 0.01 cm ，这时每天大约快多少？

35．讨论函数 $f(x)=|x|^{3}$ 在点 $x=0$ 处的各阶导数．
36．求下列函数的 $n$ 阶导数：
（1）$y=\ln a^{x}(a>0, a \neq 1)$ ；
（2）$y=\frac{1}{x^{2}-3 x+2}$ ；
（3）$y=\frac{1+x}{\sqrt[3]{1-x}}$ ；
（4）$y=\sin ^{3} x$ ；
（5）$y=\mathrm{e}^{x} \sin x$ ；
（6）$y=\frac{x^{n}}{1-x}$ ；
（7）$y=\frac{x^{n}}{x^{2}-1}$ ；
（8）$y=\frac{\ln x}{x}$ ．

37．设 $y=\left(x+\sqrt{1+x^{2}}\right)^{m}$ ，求证：

$$
\left(1+x^{2}\right) y^{\prime \prime}+x y^{\prime}=m^{2} y .
$$

38．证明切比雪夫（Chebyshev）多项式 $T_{n}(x)=\frac{1}{2^{n-1}} \cos (n \arccos x)$满足方程

$$
\left(1-x^{2}\right) T_{n}^{\prime \prime}(x)-x T_{n}^{\prime}(x)+n^{2} T_{n}(x)=0 .
$$

39．求由下列方程所确定的隐函数 $y(x)$ 的二阶导数 $y^{\prime \prime}$ ：
（1）$\sqrt[3]{x^{2}}+\sqrt[3]{y^{2}}=\sqrt[3]{a^{2}}(a>0)$ ；
（2）$x^{3}+y^{3}-3 a x y=0(a>0)$ ．

40．设函数 $y=f(x)$ 由参数方程

$$
\left\{\begin{array}{l}
x=2 t+|t|, \\
y=16 t^{4}+9 t^{3} \sin |t|,
\end{array} \quad-\infty<t<+\infty\right.
$$

确定，计算 $y=f(x)$ 在点 $x=0$ 处的各阶导数．
41．求证：
（1）若 $y=x^{n-1} \ln x$ ，则 $y^{(n)}=\frac{(n-1)!}{x}$ ；
（2）若 $y=\frac{a x+b}{c x+d}$ ，则 $y^{(n)}=(-1)^{n} \frac{n!c^{n-1}}{(c x+d)^{n+1}}(b c-a d)$ ；
（3）若 $y=x^{n-1} \mathrm{e}^{\frac{1}{x}}$ ，则 $y^{(n)}=\frac{(-1)^{n}}{x^{n+1}} \mathrm{e}^{\frac{1}{x}}$ ．
42．设 $n$ 为正整数，

$$
f(x)= \begin{cases}x^{n} \sin (\ln |x|), & x \neq 0 \\ 0, & x=0\end{cases}
$$

求证 $f(x)$ 在点 $x=0$ 处有直到 $n-1$ 阶导数，但无 $n$ 阶导数．
43．求证：
（1）$\sum_{k=0}^{n} k \mathrm{C}_{n}^{k} x^{k}(1-x)^{n-k}=n x$ ；
（2）$\sum_{k=0}^{n} k(k-1) \mathrm{C}_{n}^{k} x^{k}(1-x)^{n-k}=n(n-1) x^{2}$ ；
（3）$\sum_{k=0}^{n}(k-n x)^{2} \mathrm{C}_{n}^{k} x^{k}(1-x)^{n-k}=n x(1-x)$ ．
44．设函数 $f(x)$ 在点 $x=0$ 处连续，而且

$$
\lim _{x \rightarrow 0} \frac{f(2 x)-f(x)}{x}=m .
$$

求证 $f^{\prime}(0)=m$ ．
45．设函数 $y=f(x)$ 是严格单调的三阶可导函数，而且 $f^{\prime}(x) \neq 0$ ，求 $\left(f^{-1}\right)^{(3)}(y)$ ．

## 第五章 导数的应用

上一章中，我们着重介绍了导数的概念及计算导数的各种方法．这一章，我们将主要利用导数来研究函数的性质．

## §5．1 微分中值定理

微分中值定理是反映函数和导数之间联系的重要定理，是利用导数研究函数的桥梁，因此它是一个强有力的工具。灵活地运用它可使许许多多问题的解决变得轻而易举。在建立微分中值定理时需要用到如下的费马（Fermat）定理，因此我们先来介绍这一定理。

## 5．1．1 费马定理

极值问题是数学中的重要研究对象，在本章中我们将利用导数来讨论此类问题。为此我们引入

定义 5．1．1 若函数 $f(x)$ 在 $x_{0}$ 的某个去心邻域 $U_{0}\left(x_{0}, \delta\right)$ 内恒有

$$
f(x) \leqslant f\left(x_{0}\right)\left(f(x) \geqslant f\left(x_{0}\right)\right)
$$

则称 $x_{0}$ 为 $f(x)$ 的极大（小）值点，$f\left(x_{0}\right)$ 称为它的极大（小）值；若在上述定义中，不等号严格成立，则称 $f\left(x_{0}\right)$ 为严格极大（小）值。极大值点和极小值点统称为极值点，极大值和极小值统称为极值．

从图像上来看，一个函数 $y=f(x)$ 若在 $x_{0}$ 处取极值，并且该函数的图像在点 $\left(x_{0}, f\left(x_{0}\right)\right)$ 处存在切线，则该切线一定是水平的．事实上，我们有

定理 5．1．1（费马定理）设函数 $f(x)$ 在 $U\left(x_{0}, \delta\right)$ 中有定义．若 $x_{0}$是 $f(x)$ 的极值点，且 $f^{\prime}\left(x_{0}\right)$ 存在，则 $f^{\prime}\left(x_{0}\right)=0$ ．

证明 无妨设 $f\left(x_{0}\right)$ 为极大值，则当 $\Delta x>0$ 且 $x_{0}+\Delta x \in U\left(x_{0}, \delta\right)$时，有

$$
\frac{f\left(x_{0}+\Delta x\right)-f\left(x_{0}\right)}{\Delta x} \leqslant 0 .
$$

令 $\Delta x \rightarrow 0+0$ ，得 $f^{\prime}\left(x_{0}\right) \leqslant 0$ ．
当 $\Delta x<0$ 且 $x_{0}+\Delta x \in U\left(x_{0}, \delta\right)$ 时，有

$$
\frac{f\left(x_{0}+\Delta x\right)-f\left(x_{0}\right)}{\Delta x} \geqslant 0 .
$$

令 $\Delta x \rightarrow 0-0$ ，得 $f^{\prime}\left(x_{0}\right) \geqslant 0$ ．
综上所证，推得 $f^{\prime}\left(x_{0}\right)=0$ 。
注 使得 $f^{\prime}(x)=0$ 的点 $x_{0}$ 也称为 $f(x)$ 的驻点．费马定理告诉我们，若极值点可导，则它必是驻点．例子 $y=x^{3}$ 在 $x=0$ 处的性质告诉我们，驻点未必是极值点．

## 5．1．2 罗尔微分中值定理

定理 5．1．2（罗尔（Rolle）微分中值定理）设函数 $f(x)$ 在 $[a, b]$上连续，在 $(a, b)$ 内可导，且 $f(a)=f(b)$ ，则在 $(a, b)$ 内至少存在一点 $\xi$ ，使得

$$
f^{\prime}(\xi)=0
$$

证明 因为 $f(x)$ 在 $[a, b]$ 连续，所以 $f(x)$ 在 $[a, b]$ 上有最大值 $M$与最小值 $m$ ．如果 $M=m$ ，则 $f(x)$ 在 $[a, b]$ 为一常值函数，从而 $f^{\prime}(x) \equiv 0, \forall x \in[a, b]$ ．此时，我们可取 $(a, b)$ 中任意一点作为 $\xi$ ．如果 $M>m$ ，则 $M$ 与 $m$ 至少有一个不等于 $f(a)=f(b)$ ，不妨设 $M>f(a)$ 。这时，必存在 $\xi \in(a, b)$ ，使得 $f(\xi)=M$ ，即 $\xi$ 为 $f(x)$ 的一个极值点．再由 $f^{\prime}(\xi)$ 存在，并且应用费马定理，得 $f^{\prime}(\xi)=0$ ．

罗尔微分中值定理的几何意义是，在一段每点都有切线的曲线上，若两端点的高度相同，则在此曲线上至少存在一条水平切线（参见图 5．1．1）．

此外，罗尔微分中值定理只是告诉我们在区间 $(a, b)$ 内存在一点 $\xi$使得 $f^{\prime}(\xi)=0$ ，但并没有告诉我们怎样去求这一 $\xi$ ．事实上，一般来讲，

要将这一 $\xi$ 求出来是十分困难．其次，在很多时候我们会发现，满足这样要求的 $\xi$ 可能有很多，甚至有可能是无穷多个。

![](https://cdn.mathpix.com/cropped/f397bc0d-50c3-4383-9d6c-d4e0878fb0fc-13.jpg?height=690&width=1028&top_left_y=724&top_left_x=780)
图 5．1．1

例 5．1．1 设函数 $f(x)$ 在 $(a, b)$ 上可微，证明 $f(x)$ 在 $(a, b)$ 内的两个零点之间必有 $f(x)+f^{\prime}(x)$ 的零点．

证明 令 $F(x)=\mathrm{e}^{x} f(x)$ ，由罗尔微分中值定理知，在 $f(x)$ 的两个零点之间必有

$$
F^{\prime}(x)=\mathrm{e}^{x}\left(f(x)+f^{\prime}(x)\right)
$$

的零点，也即有 $f(x)+f^{\prime}(x)$ 的零点．
例 5．1．2 设 $P_{n}(x)$ 为一个 $n$ 次多项式，证明 $\mathrm{e}^{x}-P_{n}(x)=0$ 至多有 $n+1$ 个不同的根．

证明 令 $f(x)=\mathrm{e}^{x}-P_{n}(x)$ 。假若 $f(x)$ 有 $n+2$ 个不同的零点，则由罗尔微分中值定理知，$f^{\prime}(x)=\mathrm{e}^{x}-\left(P_{n}(x)\right)^{\prime}$ 至少有 $n+1$ 个不同的零点；然后再应用罗尔微分中值定理，又有 $f^{\prime \prime}(x)=\mathrm{e}^{x}-\left(P_{n}(x)\right)^{\prime \prime}$ 至少有 $n$ 个不同的零点．如此下去，即知 $f^{(n+1)}=\mathrm{e}^{x}-\left(P_{n}(x)\right)^{(n+1)}$ 至少有一个零点．由于 $P_{n}(x)$ 是一个 $n$ 次多项式，所以这就蕴涵着 $\mathrm{e}^{x}$ 必有一个零点，这显然是不可能的．这一矛盾说明 $\mathrm{e}^{x}-P_{n}(x)=0$ 至多有 $n+1$ 个不同的根．

例 5．1．3 设函数 $f(x)$ 在 $[0,1]$ 上连续，在 $(0,1)$ 内可导，且 $f(0)= f(1)=0$ ．再假设 $f\left(t_{0}\right)=\alpha$ ，其中 $t_{0} \in(0,1)$ ．证明：存在 $\xi \in(0,1)$ ，使得 $f^{\prime}(\xi)=\alpha$ ．

证明 若 $\alpha=0$ ，利用罗尔微分中值定理即得．
现设 $\alpha \neq 0$ 。构造辅助函数

$$
F(x)=f(x)-\alpha x
$$

则 $F(x)$ 在 $[0,1]$ 上连续，在 $(0,1)$ 内可导，并且有

$$
F(0)=0, \quad F(1)=-\alpha, \quad F\left(t_{0}\right)=\left(1-t_{0}\right) \alpha .
$$

由于 $F(1) F\left(t_{0}\right)<0$ ，因此在 $\left(t_{0}, 1\right)$ 中必存在 $\eta$ ，使得 $F(\eta)=0$ ．
因此我们在 $[0, \eta]$ 上对 $F(x)$ 应用罗尔微分中值定理，可知在 $(0, \eta) \subset (0,1)$ 中存在 $\xi$ ，使得 $F^{\prime}(\xi)=0$ ，即 $f^{\prime}(\xi)=\alpha$ ．

## 5．1．3 拉格朗日微分中值定理

罗尔微分中值定理要求所考虑的函数在所讨论区间的两端点有相同的函数值，这给它的应用范围带来很大的限制．因此，一个自然的问题就是：如果将罗尔微分中值定理中的条件＂$f(a)=f(b)$＂去掉，将有什么结论呢？其实，如果将坐标轴去掉，纯粹从几何上来看，罗尔微分中值定理是说，在一段每点都有切线的曲线上至少有一点的切线平行于两端点的连线。而这正是一个可微函数的图像所具有的几何特征。因此，如果将罗尔微分中值定理中的条件＂$f(a)=f(b)$＂去掉，则有如下的定理：

定理 5．1．3（拉格朗日（Lagrange）微分中值定理）若函数 $f(x)$在 $[a, b]$ 上连续，在 $(a, b)$ 内可导，则在 $(a, b)$ 内至少存在一点 $\xi$ ，使得

$$
f^{\prime}(\xi)=\frac{f(b)-f(a)}{b-a}
$$

证明 考查 $f(x)$ 减去过 $(a, f(a))$ 和 $(b, f(b))$ 这两点的直线所确定的线性函数而得到的函数：

$$
F(x)=f(x)-\left[f(a)+\frac{f(b)-f(a)}{b-a}(x-a)\right]
$$

容易验证，这样定义的函数 $F(x)$ 满足罗尔微分中值定理的所有条件．因此，存在 $\xi \in(a, b)$ ，使得

$$
F^{\prime}(\xi)=f^{\prime}(\xi)-\frac{f(b)-f(a)}{b-a}=0,
$$

即

$$
f^{\prime}(\xi)=\frac{f(b)-f(a)}{b-a} .
$$

注（1）在上述定理中，若我们只假定 $f(x)$ 在以 $a$ 和 $b$ 为端点的闭区间满足所有的条件，不难看出，对 $a>b$ 的情形，拉格朗日微分中值定理仍具有同样的形式。
（2）公式（5．1．1）有时也称为拉格朗日中值公式．在实际应用时，常将拉格朗日中值公式写成如下形式

$$
f(a+h)-f(a)=f^{\prime}(\xi) h,
$$

这里 $h=b-a$ 可以是正数，也可以是负数，而 $\xi$ 则位于 $a$ 与 $a+h$ 之间。有时，为了避免＂$\xi$ 位于 $a$ 与 $a+h$ 之间＂可能出现的两种情形，常将上式改写成

$$
f(a+h)=f(a)+f^{\prime}(a+\theta h) h,
$$

其中 $\theta=\frac{\xi-a}{h}$ ．无论 $h$ 是正数还是负数，总有 $0<\theta<1$ ，这是因为 $\xi$位于 $a$ 与 $a+h$ 之间的缘故．

此外，拉格朗日微分中值定理的几何意义如图 5．1．2 所示，即在一段每点都有切线的曲线上至少有一点的切线平行于两个端点的连线。

![](https://cdn.mathpix.com/cropped/f397bc0d-50c3-4383-9d6c-d4e0878fb0fc-15.jpg?height=610&width=1076&top_left_y=2483&top_left_x=752)
图 5．1．2

由拉格朗日微分中值定理立即可推出如下两个十分有用的结论：

推论 1 设函数 $f(x) \in C[a, b]$ ，且在 $(a, b)$ 内可导．若 $f^{\prime}(x)=0$ ，则 $f(x) \equiv C$ ，其中 $C$ 是一常数．

证明 对 $\forall x \in[a, b]$ ，在 $[a, x]$ 上应用拉格朗日微分中值定理知，存在 $\xi \in(a, x)$ ，使得

$$
0=f^{\prime}(\xi)=\frac{f(x)-f(a)}{x-a}
$$

由此即得

$$
f(x) \equiv f(a) \equiv C
$$

推论 1 的结论似乎是显然的，但如果不利用微分中值定理，似乎很难证明它．

推论 2 设函数 $f(x), g(x) \in C[a, b]$ ，且在 $(a, b)$ 内可导．若 $f^{\prime}(x)= g^{\prime}(x)$ ，则 $f(x)=g(x)+C$ ，其中 $C$ 是一常数．

证明 对 $f(x)-g(x)$ 应用推论 1 即得．
例 5．1．4 证明当 $x>-1$ 且 $x \neq 0$ 时，有 $\frac{x}{1+x}<\ln (1+x)<x$ ．
证明 对 $\ln (1+x)$ ，在 0 和 $x$ 之间应用拉格朗日微分中值定理，得

$$
\frac{\ln (1+x)}{x}=\frac{\ln (1+x)-\ln 1}{x-0}=\left.(\ln (1+x))^{\prime}\right|_{x=\xi}=\frac{1}{1+\xi},
$$

其中 $\xi$ 在 0 和 $x$ 之间．再注意到，当 $x>0$ 时，有

$$
\frac{1}{1+x}<\frac{1}{1+\xi}<1
$$

而当 $-1<x<0$ 时，有

$$
1<\frac{1}{1+\xi}<\frac{1}{1+x}
$$

便知所证不等式成立。
例 5．1．5 证明 $f(x)=x^{\alpha}(0<\alpha<1)$ 在 $[0,+\infty)$ 上一致连续。
证明 由于 $f(x)$ 在 $[0,+\infty)$ 上连续，因此它在 $[0,2]$ 上一致连续．于是，$\forall \varepsilon>0, \exists \delta_{1}>0$ ，使得当 $x_{1}, x_{2} \in[0,2]$ 且 $\left|x_{1}-x_{2}\right|<\delta_{1}$ 时，有

$$
\left|f\left(x_{1}\right)-f\left(x_{2}\right)\right|<\varepsilon .
$$

另一方面，因为 $f^{\prime}(x)=\alpha x^{\alpha-1}$ 在 $[1,+\infty)$ 上严格单调递减，所以在 $[1,+\infty)$ 上恒有 $\left|f^{\prime}(x)\right| \leqslant \alpha<1$ ．于是，对 $\forall x_{1}, x_{2} \in[1,+\infty)$ ，应用拉格朗日微分中值定理，得

$$
\left|f\left(x_{1}\right)-f\left(x_{2}\right)\right|=\left|f^{\prime}(\xi)\left(x_{1}-x_{2}\right)\right| \leqslant\left|x_{1}-x_{2}\right|
$$

这样，对给定的 $\varepsilon$ ，取 $\delta_{2}=\varepsilon$ ，则当 $x_{1}, x_{2} \in[1,+\infty)$ 且 $\left|x_{1}-x_{2}\right|<\delta_{2}$时，有

$$
\left|f\left(x_{1}\right)-f\left(x_{2}\right)\right|<\varepsilon
$$

现取 $\delta=\min \left\{\delta_{1}, \delta_{2}, 1\right\}$ ，则 $\forall x_{1}, x_{2} \in[0,+\infty)$ ，当 $\left|x_{1}-x_{2}\right|<\delta$ 时，一定有 $x_{1}, x_{2} \in[0,2]$ 或 $x_{1}, x_{2} \in[1,+\infty)$ ，从而必有 $\left|f\left(x_{1}\right)-f\left(x_{2}\right)\right|<\varepsilon$ 。这表明 $f(x)$ 在 $[0,+\infty)$ 上一致连续．

注 从上例的证明中可以看出：若一个函数在某区间上的导数有界，则它必在该区间上一致连续。此外，上例也说明了，一致连续的函数即使导数存在，其导数也未必在所论区间上有界。

思考题 当 $\alpha>1$ 时，函数 $f(x)=x^{\alpha}$ 是否在 $[0,+\infty)$ 上一致连续？

例 5．1．6 证明：对任意的 $x \in \mathbb{R}$ ，有

$$
\arctan x=\arcsin \left(\frac{x}{\sqrt{1+x^{2}}}\right)
$$

证明 令

$$
f(x)=\arctan x-\arcsin \left(\frac{x}{\sqrt{1+x^{2}}}\right)
$$

则有

$$
f^{\prime}(x)=\frac{1}{1+x^{2}}-\frac{1}{\sqrt{1-\frac{x^{2}}{1+x^{2}}}} \frac{\sqrt{1+x^{2}}-\frac{x^{2}}{\sqrt{1+x^{2}}}}{1+x^{2}}=0, \quad \forall x \in \mathbb{R}
$$

于是由推论 1 知 $f(x) \equiv C$ ，而 $f(0)=0$ ，故所证等式成立．

## 5．1．4 柯西微分中值定理

为了将拉格朗日微分中值定理作进一步的推广，我们现在换个观点来看这个定理．在该定理的条件下，我们有 $f^{\prime}\left(\xi_{1}\right)=\frac{f(b)-f(a)}{b-a}$ ，其分子是函数 $f(x)$ 在区间端点之值的差，而其分母可以看成函数 $y=x$在端点之值的差。从这个观点来看，一个自然的问题是：能否将 $y=x$换成一个任意的函数呢？下面的柯西微分中值定理回答了这个问题．

定理 5．1．4（柯西微分中值定理）若函数 $f(x)$ 和 $g(x)$ 在 $[a, b]$上连续，在 $(a, b)$ 内可导，而且 $g^{\prime}(x) \neq 0$ ，则在 $(a, b)$ 内至少存在一点 $\xi$ ，使得

$$
\frac{f(b)-f(a)}{g(b)-g(a)}=\frac{f^{\prime}(\xi)}{g^{\prime}(\xi)}
$$

注 分别应用拉格朗日微分中值定理于 $f(x)$ 和 $g(x)$ ，我们可得

$$
\frac{f(b)-f(a)}{g(b)-g(a)}=\frac{f^{\prime}\left(\xi_{1}\right)}{g^{\prime}\left(\xi_{2}\right)} .
$$

注意，这里的 $\xi_{1}$ 与 $\xi_{2}$ 可能是不一样的！因此，我们不能简单地应用拉格朗日微分中值定理来证明本定理。

证明 对函数 $g(x)$ 应用拉格朗日微分中值定理（或罗尔微分中值定理），可知 $g^{\prime}(x) \neq 0$ 蕴涵着 $g(a) \neq g(b)$ ．仿照拉格朗日中值定理的证明方法，令

$$
G(x)=f(x)-\left[f(a)+\frac{f(b)-f(a)}{g(b)-g(a)}(g(x)-g(a))\right],
$$

则 $G(x)$ 满足罗尔定理的所有条件．于是，由罗尔定理知，存在 $\xi \in(a, b)$ ，使得 $G^{\prime}(\xi)=0$ 。整理即得公式（5．1．2）。

柯西微分中值定理的几何意义是，若 $u v$ 坐标平面的曲线由参数方程

$$
\left\{\begin{array}{l}
u=g(x), \\
v=f(x),
\end{array} \quad x \in[a, b]\right.
$$

给出，其中 $f(x)$ 和 $g(x)$ 满足定理的条件，则存在 $\xi \in(a, b)$ ，使得过点 $(g(\xi), f(\xi))$ 的切线平行于两端点的连线．

此外，类似于拉格朗日微分中值定理，柯西微分中值定理也常写成如下形式：

$$
\frac{f(b)-f(a)}{g(b)-g(a)}=\frac{f^{\prime}(a+\theta(b-a))}{g^{\prime}(a+\theta(b-a))}
$$

其中 $0<\theta<1$ ，且不要求 $b>a$ ．
例 5．1．7 设函数 $f(x)$ 在区间 $(0,1]$ 上连续，在区间 $(0,1)$ 内可导，而且 $\lim _{x \rightarrow 0+0} \sqrt{x} f^{\prime}(x)=\ell$ ．证明 $f(x)$ 在区间 $(0,1]$ 上一致连续．

证明 由 $\lim _{x \rightarrow 0+0} \sqrt{x} f^{\prime}(x)=\ell$ 可知，存在 $M>0$ 和 $0<c<1$ ，使得

$$
\left|\sqrt{x} f^{\prime}(x)\right| \leqslant M, \quad \forall x \in(0, c]
$$

现任取 $x_{1}, x_{2} \in(0, c], x_{1} \neq x_{2}$ ，应用柯西微分中值定理，可得

$$
\frac{f\left(x_{1}\right)-f\left(x_{2}\right)}{\sqrt{x_{1}}-\sqrt{x_{2}}}=\left.\frac{f^{\prime}(x)}{(\sqrt{x})^{\prime}}\right|_{x=\xi}=2 \sqrt{\xi} f^{\prime}(\xi),
$$

其中 $\xi$ 位于 $x_{1}$ 和 $x_{2}$ 之间．于是，有

$$
\left|f\left(x_{1}\right)-f\left(x_{2}\right)\right| \leqslant 2 M\left|\sqrt{x_{1}}-\sqrt{x_{2}}\right|
$$

当然，上式对 $x_{1}=x_{2}$ 亦成立．再注意到

$$
\begin{aligned}
\left|\sqrt{x_{1}}-\sqrt{x_{2}}\right|^{2} & \leqslant\left|\sqrt{x_{1}}-\sqrt{x_{2}}\right|\left|\sqrt{x_{1}}+\sqrt{x_{2}}\right| \\
& =\left|x_{1}-x_{2}\right|
\end{aligned}
$$

对 $\forall \varepsilon>0$ ，只需取 $\delta=\frac{\varepsilon^{2}}{(2 M)^{2}}$ ，则当 $x_{1}, x_{2} \in(0, c]$ 且 $\left|x_{1}-x_{2}\right|<\delta$ 时，便有

$$
\begin{aligned}
\left|f\left(x_{1}\right)-f\left(x_{2}\right)\right| & \leqslant 2 M\left|\sqrt{x_{1}}-\sqrt{x_{2}}\right| \\
& \leqslant 2 M\left|x_{1}-x_{2}\right|^{\frac{1}{2}}<\varepsilon
\end{aligned}
$$

这表明 $f(x)$ 在 $(0, c]$ 上一致连续．又 $f(x)$ 在区间 $(0,1]$ 上连续蕴涵着 $f(x)$ 在区间 $[c, 1]$ 上一致连续，从而便有 $f(x)$ 在区间 $(0,1]$ 上一致连续．

从本节内容可以看出，罗尔微分中值定理是一个基本的结果，拉格朗日微分中值定理是罗尔微分中值定理的直接推广，而柯西微分中值定理则是拉格朗日微分中值定理的一个重要推广。它们各自适合不同的场合，是我们利用导数研究函数的强有力工具．

## §5．2 洛必达法则

在建立求导法则和求导公式的过程中，极限的理论和一些具体的极限起着决定性的作用．而有了导数理论和求导公式之后，又可利用它解决极限理论中某些不定式的极限问题．

大家已经知道，如果我们已知

$$
\lim _{x \rightarrow a} f(x)=A, \quad \lim _{x \rightarrow a} g(x)=B,
$$

这里 $A, B, a$ 可以是有限数或无穷大，那么除去某些例外情形，我们可以利用下面的极限运算法则去求它们的和、差、积、商及幂－指数函数式的极限：
（1） $\lim _{x \rightarrow a} f(x) \pm g(x)=A \pm B$ ；
（2） $\lim _{x \rightarrow a} f(x) \cdot g(x)=A B$ ；
（3） $\lim _{x \rightarrow a} \frac{f(x)}{g(x)}=\frac{A}{B}(B \neq 0)$ ；
（4） $\lim _{x \rightarrow a} f(x)^{g(x)}=A^{B}$ ．
对于以上各式，所说的例外情况分别为：
（a）在（1）中出现两个同号无穷大相减；
（b）在（2）中 $A$ 与 $B$ 之一为 0 ，另一为无穷大；
（c）在（3）中 $A$ 与 $B$ 同时为 0 ，或者同为无穷大；
（d）在（4）中 $A=1, B=\infty$ ，或者 $A=B=0$ ，或者 $A=\infty, B=0$ ．
这些例外情形所涉及的极限类型统称为不定式，分别记为：

$$
\infty-\infty, \quad 0 \cdot \infty, \quad \frac{0}{0}, \quad \frac{\infty}{\infty}, \quad 1^{\infty}, \quad 0^{0}, \quad \infty^{0}
$$

所有这些不定式本质上只有一种：$\frac{0}{0}$ ．其他的不定式经过简单的变

换可以化为这种不定式，如 $0 \cdot \infty=0 \cdot \frac{1}{0}$ 就成为 $\frac{0}{0}$ 型，或者 $0 \cdot \infty=\frac{1}{\infty} \cdot \infty$变成 $\frac{\infty}{\infty}$ 型，而 $\frac{\infty}{\infty}=\frac{1}{0} / \frac{1}{0}=\frac{0}{0}$ ，等等。下面我们利用柯西微分中值定理来给出这种不定式的定值方法——洛必达（L＇Hospital）法则。以后我们还会发现，虽然 $\frac{\infty}{\infty}$ 型可以化为 $\frac{0}{0}$ 型，但要利用洛必达法则，我们还必须对 $\frac{\infty}{\infty}$ 的情况进行研究。

## 5．2．1 $\frac{\mathbf{0}}{\mathbf{0}}$ 型不定式

定理 5．2．1 设函数 $f(x)$ 和 $g(x)$ 在 $a$ 点的某一去心邻域 $U_{0}(a, \delta)$上可导，而且满足：
（1） $\lim _{x \rightarrow a} f(x)=\lim _{x \rightarrow a} g(x)=0$ ；
（2）$g^{\prime}(x) \neq 0, \forall x \in U_{0}(a, \delta)$ ；
（3） $\lim _{x \rightarrow a} \frac{f^{\prime}(x)}{g^{\prime}(x)}=\ell$（ $\ell$ 为有限数或 $\pm \infty, \infty$ ），
则有

$$
\lim _{x \rightarrow a} \frac{f(x)}{g(x)}=\lim _{x \rightarrow a} \frac{f^{\prime}(x)}{g^{\prime}(x)}=\ell .
$$

证明 先考虑 $\ell$ 为有限数的情形，并且我们先证 $\lim _{x \rightarrow a-0} \frac{f(x)}{g(x)}=\ell$ ．由条件（1）知，若补充定义 $f(a)=g(a)=0$ ，则 $f(x), g(x)$ 在 $a$ 点连续．于是，对 $\forall x \in(a-\delta, a)$ ，在区间 $[x, a]$ 上应用柯西微分中值定理，有

$$
\frac{f(x)}{g(x)}=\frac{f(x)-f(a)}{g(x)-g(a)}=\frac{f^{\prime}(\xi)}{g^{\prime}(\xi)}, \quad x<\xi<a
$$

又由条件（3），可得

$$
\lim _{x \rightarrow a-0} \frac{f^{\prime}(\xi)}{g^{\prime}(\xi)}=\lim _{x \rightarrow a-0} \frac{f^{\prime}(x)}{g^{\prime}(x)}=\ell
$$

所以

$$
\lim _{x \rightarrow a-0} \frac{f(x)}{g(x)}=\ell
$$

同理可证 $\lim _{x \rightarrow a+0} \frac{f(x)}{g(x)}=\ell$ 。综合起来就有 $\lim _{x \rightarrow a} \frac{f(x)}{g(x)}=\ell$ 。
当 $\ell=+\infty,-\infty$ 或 $\infty$ 时，证明类似，请读者作为练习自己给出．

注 从定理的证明可以看出，把 $x \rightarrow a$ 改为 $x \rightarrow a-0$ 或 $x \rightarrow a+0$ ，上述定理的结论仍然成立。

当 $x \rightarrow \infty$ 时，我们有
定理 5．2．2 设函数 $f(x), g(x)$ 在 $U=\{x:|x|>a>0\}$ 上可导，而且满足：
（1） $\lim _{x \rightarrow \infty} f(x)=\lim _{x \rightarrow \infty} g(x)=0$ ；
（2）$g^{\prime}(x) \neq 0, \forall x \in U$ ；
（3） $\lim _{x \rightarrow \infty} \frac{f^{\prime}(x)}{g^{\prime}(x)}=\ell$（ $\ell$ 为有限数或 $\pm \infty, \infty$ ），
则有

$$
\lim _{x \rightarrow \infty} \frac{f(x)}{g(x)}=\ell
$$

证明 我们先证 $x \rightarrow+\infty$ 的情形。作自变量变换 $x=\frac{1}{t}$ ，则 $x \rightarrow +\infty$ 相应于 $t \rightarrow 0+0$ ．于是有

$$
\lim _{t \rightarrow 0+0} \frac{f^{\prime}\left(\frac{1}{t}\right)}{g^{\prime}\left(\frac{1}{t}\right)}=\lim _{x \rightarrow+\infty} \frac{f^{\prime}(x)}{g^{\prime}(x)}
$$

并且由条件（1），有

$$
\lim _{t \rightarrow 0+0} f\left(\frac{1}{t}\right)=0, \quad \lim _{t \rightarrow 0+0} g\left(\frac{1}{t}\right)=0 .
$$

应用定理 5．2．1 于开区间 $\left(0, \frac{1}{a}\right)$ 上新变量 $t$ 的函数 $f\left(\frac{1}{t}\right)$ 和 $g\left(\frac{1}{t}\right)$ ，并注意到它们关于 $t$ 的导数为

$$
f^{\prime}\left(\frac{1}{t}\right)\left(-\frac{1}{t^{2}}\right), \quad g^{\prime}\left(\frac{1}{t}\right)\left(-\frac{1}{t^{2}}\right),
$$

可得

$$
\lim _{t \rightarrow 0+0} \frac{f\left(\frac{1}{t}\right)}{g\left(\frac{1}{t}\right)}=\lim _{t \rightarrow 0+0} \frac{f^{\prime}\left(\frac{1}{t}\right)\left(-\frac{1}{t^{2}}\right)}{g^{\prime}\left(\frac{1}{t}\right)\left(-\frac{1}{t^{2}}\right)}
$$

$$
=\lim _{t \rightarrow 0+0} \frac{f^{\prime}\left(\frac{1}{t}\right)}{g^{\prime}\left(\frac{1}{t}\right)}=\lim _{x \rightarrow+\infty} \frac{f^{\prime}(x)}{g^{\prime}(x)}=\ell
$$

因此，我们有

$$
\lim _{x \rightarrow+\infty} \frac{f(x)}{g(x)}=\ell
$$

立．

同理可证 $x \rightarrow-\infty$ 的情形．这样我们就证明了 $\lim _{x \rightarrow \infty} \frac{f(x)}{g(x)}=\ell$ 成
注 从定理的证明可以看出，把 $x \rightarrow \infty$ 改为 $x \rightarrow-\infty$ 或 $x \rightarrow +\infty$ ，上述定理的结论仍然成立。

例5．2．1 求极限 $\lim _{x \rightarrow 0} \frac{\sinh x-x}{\sin x-x}$ ．
解 这是一个 $\frac{0}{0}$ 型不定式．应用洛必达法则，可得

$$
\begin{aligned}
\lim _{x \rightarrow 0} \frac{\sinh x-x}{\sin x-x} & =\lim _{x \rightarrow 0} \frac{\cosh x-1}{\cos x-1}=\lim _{x \rightarrow 0} \frac{\sinh x}{-\sin x} \\
& =\lim _{x \rightarrow 0} \frac{\cosh x}{-\cos x}=-1
\end{aligned}
$$

例 5．2．2 求极限 $\lim _{x \rightarrow 0+0} \frac{\sqrt{x}}{1-\mathrm{e}^{2 \sqrt{x}}}$ ．
解 这也是一个 $\frac{0}{0}$ 型不定式．令 $t=\sqrt{x}$ ，则有

$$
\lim _{x \rightarrow 0+0} \frac{\sqrt{x}}{1-\mathrm{e}^{2 \sqrt{x}}}=\lim _{t \rightarrow 0+0} \frac{t}{1-\mathrm{e}^{2 t}}=\lim _{t \rightarrow 0+0} \frac{1}{-2 \mathrm{e}^{2 t}}=-\frac{1}{2}
$$

例 5．2．3 求极限 $\lim _{x \rightarrow+\infty} x\left(\frac{\pi}{2}-\arctan x\right)$ ．
解 这是一个 $0 \cdot \infty$ 型不定式．先将其转化为 $\frac{0}{0}$ 型不定式，然后应用洛必达法则，可得

$$
\lim _{x \rightarrow+\infty} x\left(\frac{\pi}{2}-\arctan x\right)=\lim _{x \rightarrow+\infty} \frac{\frac{\pi}{2}-\arctan x}{\frac{1}{x}}=\lim _{x \rightarrow+\infty} \frac{-\frac{1}{1+x^{2}}}{-\frac{1}{x^{2}}}=1
$$

## 5．2．2 $\frac{\infty}{\infty}$ 型不定式

从理论上来讲，$\frac{\infty}{\infty}$ 型不定式可化为 $\frac{0}{0}$ 型．但是有时候这样做会使问题更为复杂，致使问题的解决失败。例如，求 $\lim _{x \rightarrow+\infty} \frac{\ln x}{x^{\alpha}}(\alpha>0)$ 。如果将它化成

$$
\frac{\ln x}{x^{\alpha}}=\frac{\frac{1}{x^{\alpha}}}{\frac{1}{\ln x}}
$$

则分子、分母分别求导后所得表达式比原来的函数还要复杂．因此，我们有必要专门研究求 $\frac{\infty}{\infty}$ 型不定式极限的方法．

定理 5．2．3 设函数 $f(x), g(x)$ 在 $a$ 点的某一去心邻域 $U_{0}\left(a, \delta_{0}\right) \left(\delta_{0}>0\right)$ 上可导，且满足：
（1） $\lim _{x \rightarrow a} g(x)=\infty$ ；
（2）$g^{\prime}(x) \neq 0, \forall x \in U_{0}\left(a, \delta_{0}\right)$ ；
（3） $\lim _{x \rightarrow a} \frac{f^{\prime}(x)}{g^{\prime}(x)}=\ell(\ell$ 有限数或 $\pm \infty, \infty)$ ，
则有

$$
\lim _{x \rightarrow a} \frac{f(x)}{g(x)}=\ell
$$

证明 只对 $\ell \in \mathbb{R}$ 和 $x \rightarrow a-0$ 的情形证明，其他的情形请读者自己给出证明．
$\forall \varepsilon>0$ ，由条件（2）和（3）知，$\exists \delta_{1}>0,0<\delta_{1}<\delta_{0}$ ，当 $a-\delta_{1}<\zeta<a$时，有

$$
\left|\frac{f^{\prime}(\zeta)}{g^{\prime}(\zeta)}-\ell\right|<\frac{\varepsilon}{3} .
$$

对已经取定的 $\delta_{1}$ 及 $x \in\left(a-\delta_{1}, a\right)$ ，在 $\left[a-\delta_{1}, x\right]$ 上应用柯西微分中值定理，存在 $\xi \in\left[a-\delta_{1}, x\right]$ ，使

$$
\frac{f(x)-f\left(x_{1}\right)}{g(x)-g\left(x_{1}\right)}-\ell=\frac{f^{\prime}(\xi)}{g^{\prime}(\xi)}-\ell,
$$

这里 $x_{1}=a-\delta_{1}, \xi \in\left(a-\delta_{1}, a\right)$ ．上式可化为

$$
f(x)-f\left(x_{1}\right)-\ell\left[g(x)-g\left(x_{1}\right)\right]=\left[\frac{f^{\prime}(\xi)}{g^{\prime}(\xi)}-\ell\right]\left[g(x)-g\left(x_{1}\right)\right]
$$

## 整理得

$$
f(x)-\ell g(x)=\left[f\left(x_{1}\right)-\ell g\left(x_{1}\right)\right]+\left[\frac{f^{\prime}(\xi)}{g^{\prime}(\xi)}-\ell\right]\left[g(x)-g\left(x_{1}\right)\right]
$$

在上式两边同除以 $g(x)$ ，可得

$$
\frac{f(x)}{g(x)}-\ell=\left[\frac{f^{\prime}(\xi)}{g^{\prime}(\xi)}-\ell\right]\left[1-\frac{g\left(x_{1}\right)}{g(x)}\right]+\frac{f\left(x_{1}\right)-\ell g\left(x_{1}\right)}{g(x)}
$$

又由于 $\lim _{x \rightarrow a-0} g(x)=\infty$ ，故对固定的 $x_{1}$ ，有

$$
\lim _{x \rightarrow a-0} \frac{f\left(x_{1}\right)-\ell g\left(x_{1}\right)}{g(x)}=0, \quad \lim _{x \rightarrow a-0} \frac{g\left(x_{1}\right)}{g(x)}=0 .
$$

所以 $\exists \delta_{2}\left(0<\delta_{2}<\delta_{1}\right)$ ，使得当 $a-\delta_{2}<x<a$ 时，有

$$
\left|\frac{f\left(x_{1}\right)-\ell g\left(x_{1}\right)}{g(x)}\right|<\frac{\varepsilon}{2}, \quad\left|\frac{g\left(x_{1}\right)}{g(x)}\right|<\frac{1}{2} .
$$

令 $\delta=\min \left\{\delta_{1}, \delta_{2}\right\}$ ，则当 $a-\delta<x<a$ 时，有

$$
\begin{aligned}
\left|\frac{f(x)}{g(x)}-\ell\right| & \leqslant\left|\frac{f^{\prime}(\xi)}{g^{\prime}(\xi)}-\ell\right|\left|1-\frac{g\left(x_{1}\right)}{g(x)}\right|+\left|\frac{f\left(x_{1}\right)-\ell g\left(x_{1}\right)}{g(x)}\right| \\
& \leqslant \frac{3}{2} \cdot \frac{\varepsilon}{3}+\frac{\varepsilon}{2}=\varepsilon .
\end{aligned}
$$

因此 $\lim _{x \rightarrow a-0} \frac{f(x)}{g(x)}=\ell$ ．
注 把 $x \rightarrow a$ 改为 $x \rightarrow a-0$ 或 $x \rightarrow a+0$ ，上述定理的结论仍然成立。

定理 5．2．4 设函数 $f(x), g(x)$ 在 $U=\{x:|x|>h>0\}$ 上可导，而且满足：
（1） $\lim _{x \rightarrow \infty} g(x)=\infty$ ；
（2）$g^{\prime}(x) \neq 0, \forall x \in U$ ；
（3） $\lim _{x \rightarrow \infty} \frac{f^{\prime}(x)}{g^{\prime}(x)}=\ell(\ell$ 有限数或 $\pm \infty, \infty)$ ，则有

$$
\lim _{x \rightarrow \infty} \frac{f(x)}{g(x)}=\lim _{x \rightarrow \infty} \frac{f^{\prime}(x)}{g^{\prime}(x)}=\ell
$$

本定理的证明类似于定理 5．2．2 的证明，请读者作为练习自己补证．

注 把 $x \rightarrow \infty$ 改为 $x \rightarrow-\infty$ 或 $x \rightarrow+\infty$ ，上述定理的结论仍然成立。

例 5．2．4 求极限 $\lim _{x \rightarrow+\infty} \frac{\ln x}{x^{\varepsilon}}$ ，其中 $\varepsilon>0$ ．
解 这是一个 $\frac{\infty}{\infty}$ 型不定式，应用定理 5．2．4，有

$$
\lim _{x \rightarrow+\infty} \frac{\ln x}{x^{\varepsilon}}=\lim _{x \rightarrow+\infty} \frac{\frac{1}{x}}{\varepsilon x^{\varepsilon-1}}=\lim _{x \rightarrow+\infty} \frac{1}{\varepsilon x^{\varepsilon}}=0
$$

例 5．2．5 求极限 $\lim _{x \rightarrow+\infty} \frac{x^{\alpha}}{\mathrm{e}^{x}}$ ，其中 $\alpha>0$ ．
解 这也是一个 $\frac{\infty}{\infty}$ 型不定式，应用定理 5．2．4，有

$$
\begin{aligned}
\lim _{x \rightarrow+\infty} \frac{x^{\alpha}}{\mathrm{e}^{x}} & =\lim _{x \rightarrow+\infty} \frac{\alpha x^{\alpha-1}}{\mathrm{e}^{x}} \\
& =\cdots \cdots \\
& =\lim _{x \rightarrow+\infty} \frac{\alpha(\alpha-1) \cdots(\alpha-[\alpha]) x^{\alpha-[\alpha]-1}}{\mathrm{e}^{x}}=0
\end{aligned}
$$

设 $I$ 是一个区间，$n$ 是一个非负整数，在今后我们用记号 $C^{n} I$ 表示 $I$ 上所有具有 $n$ 阶连续导数的函数的集合；用记号 $C^{\infty} I$ 表示 $I$ 上具有任意阶导数的函数的集合．特别地，$C^{0} I$ 表示 $I$ 上所有连续函数的集合。

例 5．2．6 设函数

$$
f(x)= \begin{cases}\mathrm{e}^{-\frac{1}{x^{2}}}, & x \neq 0 \\ 0, & x=0\end{cases}
$$

证明 $f(x) \in C^{\infty}(-\infty,+\infty)$ ．
证明 当 $x \neq 0$ 时，

$$
f^{\prime}(x)=\frac{2}{x^{3}} \mathrm{e}^{-\frac{1}{x^{2}}}
$$

当 $x=0$ 时，

$$
\begin{aligned}
f^{\prime}(0) & =\lim _{x \rightarrow 0} \frac{f(x)-f(0)}{x}=\lim _{x \rightarrow 0} \frac{\mathrm{e}^{-\frac{1}{x^{2}}}}{x}=\lim _{t \rightarrow \infty} \frac{\mathrm{e}^{-t^{2}}}{\frac{1}{t}} \quad\left(x=\frac{1}{t}\right) \\
& =\lim _{t \rightarrow \infty} \frac{t}{\mathrm{e}^{t^{2}}}=\lim _{t \rightarrow \infty} \frac{1}{2 t \mathrm{e}^{t^{2}}}=0
\end{aligned}
$$

且

$$
\lim _{x \rightarrow 0} f^{\prime}(x)=\lim _{x \rightarrow 0} \frac{2 \mathrm{e}^{-\frac{1}{x^{2}}}}{x^{3}}=\lim _{t \rightarrow \infty} \frac{2 t^{3}}{\mathrm{e}^{t^{2}}}=0,
$$

所以 $f(x) \in C^{1}(-\infty,+\infty)$ ．
继续求导，我们发现 $f^{(n)}(x)$ 具有如下形式：

$$
f^{(n)}(x)= \begin{cases}P_{3 n}\left(\frac{1}{x}\right) \mathrm{e}^{-\frac{1}{x^{2}}}, & x \neq 0, \\ 0, & x=0,\end{cases}
$$

其中 $P_{3 n}(u)$ 为变元 $u$ 的 $3 n$ 次多项式。
下面我们来证明上述断言为真．事实上，当 $n=1$ 时，我们已证它是对的．现在假定其对正整数 $n$ 成立，我们来看 $n+1$ 的情形．当 $x \neq 0$时，我们有

$$
f^{(n+1)}(x)=\left[\frac{2}{x^{3}} P_{3 n}\left(\frac{1}{x}\right)-\frac{1}{x^{2}} P_{3 n}^{\prime}\left(\frac{1}{x}\right)\right] \mathrm{e}^{-\frac{1}{x^{2}}}=P_{3(n+1)}\left(\frac{1}{x}\right) \mathrm{e}^{-\frac{1}{x^{2}}}
$$

而

$$
f^{(n+1)}(0)=\lim _{x \rightarrow 0} \frac{P_{3 n}\left(\frac{1}{x}\right) \mathrm{e}^{-\frac{1}{x^{2}}}}{x}=\lim _{t \rightarrow \infty} \frac{t P_{3 n}(t)}{\mathrm{e}^{t^{2}}}=0 \quad\left(x=\frac{1}{t}\right) .
$$

这样，我们就证明了上述断言．由此推知 $f(x)$ 任意次可微，即 $f(x) \in C^{\infty}(-\infty,+\infty)$ ．

注 上例中的函数是一个重要的函数，在今后的学习中还会多次遇到它．这个函数可以用来说明很多问题．

## 5．2．3 其他类型不定式

通过变量替换，我们总可把其他形式的不定式转化为 $\frac{0}{0}$ 型和 $\frac{\infty}{\infty}$型，具体采用什么样替换，这要对具体问题做具体分析，灵活对待。

例 5．2．7 求极限 $\lim _{x \rightarrow 0+0} x^{\alpha} \ln x(\alpha>0)$ ．
解 这是一个 $0 \cdot \infty$ 型不定式．我们有

$$
\lim _{x \rightarrow 0+0} x^{\alpha} \ln x=\lim _{x \rightarrow 0+0} \frac{\ln x}{\frac{1}{x^{\alpha}}}=\lim _{x \rightarrow 0+0} \frac{\frac{1}{x}}{-\frac{\alpha}{x^{\alpha+1}}}=\lim _{x \rightarrow 0+0}-\frac{1}{\alpha} x^{\alpha}=0 .
$$

例 5．2．8 求极限 $\lim _{x \rightarrow a}\left(\frac{a_{1}^{x}+a_{2}^{x}+\cdots+a_{n}^{x}}{n}\right)^{\frac{1}{x}}$（其中 $a_{k}>0, k= 1,2, \cdots n)$ ，这里 $a=0$ 或 $\pm \infty$ ．

解 令

$$
y=\left(\frac{a_{1}^{x}+a_{2}^{x}+\cdots+a_{n}^{x}}{n}\right)^{\frac{1}{x}}
$$

则

$$
\ln y=\frac{1}{x} \ln \left(\frac{a_{1}^{x}+a_{2}^{x}+\cdots+a_{n}^{x}}{n}\right) .
$$

下面我们分三种情况来求 $\lim _{x \rightarrow a} \ln y$ ．
（1）$x \rightarrow 0$ 的情形．此时，应用洛必达法则，有

$$
\begin{aligned}
\lim _{x \rightarrow 0} \ln y & =\lim _{x \rightarrow 0} \frac{a_{1}^{x} \ln a_{1}+a_{2}^{x} \ln a_{2}+\cdots+a_{n}^{x} \ln a_{n}}{a_{1}^{x}+a_{2}^{x}+\cdots+a_{n}^{x}} \\
& =\frac{1}{n} \ln \left(a_{1} a_{2} \cdots a_{n}\right) \\
& =\ln \sqrt[n]{a_{1} a_{2} \cdots a_{n}}
\end{aligned}
$$

所以 $\lim _{x \rightarrow 0} y=\sqrt[n]{a_{1} a_{2} \cdots a_{n}}$ ，即所求极限为这 $n$ 个数的几何平均．
（2）$x \rightarrow+\infty$ 的情形。此时，记 $M=\max \left\{a_{1}, a_{2}, \cdots, a_{n}\right\}$ ，应用洛必达法则，有

$$
\lim _{x \rightarrow+\infty} \ln y=\lim _{x \rightarrow+\infty} \frac{a_{1}^{x} \ln a_{1}+a_{2}^{x} \ln a_{2}+\cdots+a_{n}^{x} \ln a_{n}}{a_{1}^{x}+a_{2}^{x}+\cdots+a_{n}^{x}}
$$

$$
\begin{aligned}
& =\lim _{x \rightarrow+\infty} \frac{\left(\frac{a_{1}}{M}\right)^{x} \ln a_{1}+\left(\frac{a_{2}}{M}\right)^{x} \ln a_{2}+\cdots+\left(\frac{a_{n}}{M}\right)^{x} \ln a_{n}}{\left(\frac{a_{1}}{M}\right)^{x}+\left(\frac{a_{2}}{M}\right)^{x}+\cdots+\left(\frac{a_{n}}{M}\right)^{x}} \\
& =\ln M
\end{aligned}
$$

## 所以

$$
\lim _{x \rightarrow+\infty} y=M=\max \left\{a_{1}, a_{2} \cdots, a_{n}\right\}
$$

（3）$x \rightarrow-\infty$ 的情形。此时，令 $m=\min \left\{a_{1}, a_{2}, \cdots, a_{n}\right\}$ ，应用洛必达法则，有

$$
\begin{aligned}
\lim _{x \rightarrow-\infty} \ln y & =\lim _{x \rightarrow-\infty} \frac{a_{1}^{x} \ln a_{1}+a_{2}^{x} \ln a_{2}+\cdots+a_{n}^{x} \ln a_{n}}{a_{1}^{x}+a_{2}^{x}+\cdots+a_{n}^{x}} \\
& =\lim _{x \rightarrow-\infty} \frac{\left(\frac{a_{1}}{m}\right)^{x} \ln a_{1}+\left(\frac{a_{2}}{m}\right)^{x} \ln a_{2}+\cdots+\left(\frac{a_{n}}{m}\right)^{x} \ln a_{n}}{\left(\frac{a_{1}}{m}\right)^{x}+\left(\frac{a_{2}}{m}\right)^{x}+\cdots+\left(\frac{a_{n}}{m}\right)^{x}} \\
& =\ln m
\end{aligned}
$$

## 所以

$$
\lim _{x \rightarrow-\infty} y=m=\min \left\{a_{1}, a_{2}, \cdots, a_{n}\right\} .
$$

例 5．2．9 设 $x_{0} \in(0,1)$ ，定义 $x_{n}=\sin x_{n-1}, n=1,2, \cdots$ ．证明

$$
\lim _{n \rightarrow \infty} \sqrt{n} x_{n}=\sqrt{3}
$$

## 证明 首先我们来证明

$$
\lim _{x \rightarrow 0}\left(\frac{1}{\sin ^{2} x}-\frac{1}{x^{2}}\right)=\frac{1}{3}
$$

事实上，我们先将其转化为 $\frac{0}{0}$ 型不定式；然后将分母用等价无穷小量替换；最后应用洛必达法则，可得

$$
\begin{aligned}
\lim _{x \rightarrow 0}\left(\frac{1}{\sin ^{2} x}-\frac{1}{x^{2}}\right) & =\lim _{x \rightarrow 0} \frac{x^{2}-\sin ^{2} x}{x^{2} \sin ^{2} x}=\lim _{x \rightarrow 0} \frac{x^{2}-\sin ^{2} x}{x^{4}} \\
& =\lim _{x \rightarrow 0} \frac{2 x-\sin 2 x}{4 x^{3}}=\lim _{x \rightarrow 0} \frac{1-\cos 2 x}{6 x^{2}}
\end{aligned}
$$

$$
=\lim _{x \rightarrow 0} \frac{\sin 2 x}{6 x}=\lim _{x \rightarrow 0} \frac{\cos 2 x}{3}=\frac{1}{3} .
$$

其次，注意到

$$
0<x_{n+1}=\sin x_{n}<x_{n}, \quad n=1,2, \cdots
$$

利用单调收玫原理，可知该序列收玫，而且可得 $x_{n} \rightarrow 0(n \rightarrow \infty)$ 。这样，应用前面所求出的极限，可得

$$
\lim _{n \rightarrow \infty}\left(\frac{1}{x_{n+1}^{2}}-\frac{1}{x_{n}^{2}}\right)=\lim _{n \rightarrow \infty}\left(\frac{1}{\sin ^{2} x_{n}}-\frac{1}{x_{n}^{2}}\right)=\frac{1}{3} .
$$

于是，利用第二章 §2．1 中例 2．1．13 的结果有

$$
\lim _{n \rightarrow \infty} \frac{1}{n x_{n}^{2}}=\lim _{n \rightarrow \infty}\left(\frac{1}{n x_{n}^{2}}-\frac{1}{n x_{1}^{2}}\right)=\lim _{n \rightarrow \infty} \frac{1}{n} \sum_{k=1}^{n-1}\left(\frac{1}{x_{k+1}^{2}}-\frac{1}{x_{k}^{2}}\right)=\frac{1}{3},
$$

从而

$$
\lim _{n \rightarrow \infty} \sqrt{n} x_{n}=\sqrt{3}
$$

最后我们须指出的是：
（1）并非所有的 $\frac{0}{0}$ 或 $\frac{\infty}{\infty}$ 型不定式都可用洛必达法则求其极限．例如，容易证明

$$
\lim _{x \rightarrow \infty} \frac{x+\sin x}{x}=1,
$$

但是

$$
\lim _{x \rightarrow \infty} \frac{(x+\sin x)^{\prime}}{(x)^{\prime}}=\lim _{x \rightarrow \infty}(1+\cos x)
$$

不存在．因此，我们就不能用洛必达法则确定这个 $\frac{\infty}{\infty}$ 型不定式的极限．
（2）应用洛必达法则时，每步必须验证 $\lim _{x \rightarrow \infty} \frac{f^{\prime}(x)}{g^{\prime}(x)}$ 是否存在的条件，否则会得出错误的结论．例如，显然有

$$
\lim _{x \rightarrow \infty} \frac{x-\sin x}{x+\sin x}=1
$$

但若盲目地使用洛必达法则，就会得到

$$
\lim _{x \rightarrow \infty} \frac{x-\sin x}{x+\sin x}=\lim _{x \rightarrow \infty} \frac{1-\cos x}{1+\cos x}=\lim _{x \rightarrow \infty} \frac{\sin x}{-\sin x}=-1
$$

的错误结论．

